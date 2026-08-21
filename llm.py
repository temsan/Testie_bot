# -*- coding: utf-8 -*-
"""
Клиент LLM для диалога с лидом. Основной провайдер — Gemini (GEMINI_API_KEY),
с автоматическим фолбеком на OpenRouter (OPENROUTER_API_KEY), если Gemini
недоступен или упёрся в лимит бесплатного тарифа (у Gemini free tier это
всего 20 запросов/сутки на модель — легко исчерпать при живом диалоге).

Используются ДВА отдельных вызова модели за ход, а не один комбинированный:
1) reply()   — обычный разговорный ответ клиенту (следующий вопрос по сценарию).
2) extract() — отдельный узкий вызов, который анализирует весь диалог и
   возвращает строгий JSON с тем, что удалось выяснить (need/budget/timeline).

Смешивать эти задачи в одном ответе ненадёжно на маленьких бесплатных
моделях: они либо забывают вставить служебную метку, либо "протекают"
внутренним рассуждением в пользовательский текст. Два узких вызова работают
стабильнее, чем один комбинированный с хрупким форматом.
"""

import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_DEFAULT_MODEL = "gemini-3.6-flash"

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_DEFAULT_MODEL = "liquid/lfm-2.5-2.6b:free"

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


async def _complete_gemini(messages: list, json_mode: bool = False) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Не найден GEMINI_API_KEY.")
    model = os.getenv("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)

    system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages
        if m["role"] != "system"
    ]
    body = {"contents": contents}
    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            GEMINI_API_URL.format(model=model),
            params={"key": api_key},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = data["candidates"][0]["content"].get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        if not text.strip():
            raise RuntimeError(f"Gemini вернул пустой ответ: {data}")
        return text


async def _complete_openrouter(messages: list, model: str, **kwargs) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Не найден OPENROUTER_API_KEY. Задайте его в .env.")

    payload = {"model": model, "messages": messages, "max_tokens": 500, **kwargs}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"].get("content")
        if not content:
            raise RuntimeError(f"OpenRouter вернул пустой ответ: {data}")
        return content


async def _complete(messages: list, *, json_mode: bool = False, extract: bool = False) -> str:
    """Пробует Gemini первым (если задан ключ), при любой ошибке (в т.ч.
    исчерпанный дневной лимit) — молча переключается на OpenRouter."""
    if os.getenv("GEMINI_API_KEY"):
        try:
            return await _complete_gemini(messages, json_mode=json_mode)
        except Exception as exc:
            logger.warning("Gemini недоступен, переключаюсь на OpenRouter: %s", exc)

    or_model_env = "OPENROUTER_EXTRACT_MODEL" if extract else "OPENROUTER_MODEL"
    model = os.getenv(or_model_env, OPENROUTER_DEFAULT_MODEL)
    kwargs = {"temperature": 0} if extract else {"temperature": 0.5}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
        kwargs["max_tokens"] = 2500
    return await _complete_openrouter(messages, model, **kwargs)


async def reply(messages: list) -> str:
    """Обычный разговорный ответ клиенту."""
    return await _complete(messages)


async def extract(history: list, cfg: dict) -> dict:
    """Анализирует весь диалог и возвращает {"need", "budget", "timeline"} —
    budget/timeline дословно совпадают с одним из вариантов cfg или пусты."""
    budget_lines = "\n".join(f'- "{opt}"' for opt in cfg["budget_options"])
    timeline_lines = "\n".join(f'- "{opt}"' for opt in cfg["timeline_options"])
    system = (
        "Ты — модуль извлечения данных из диалога бота-консультанта с "
        "клиентом. Проанализируй диалог ниже и определи:\n"
        '- "need": краткое (3-8 слов) описание потребности клиента, или "" если не ясно;\n'
        '- "budget": РОВНО один из вариантов ниже (дословно), если бюджет уже '
        f'прозвучал в любой формулировке (в том числе просто суммой), иначе "":\n{budget_lines}\n'
        '- "timeline": РОВНО один из вариантов ниже (дословно), если сроки уже '
        f'прозвучали, иначе "":\n{timeline_lines}\n\n'
        "Ответь СТРОГО валидным JSON без каких-либо пояснений, точно в таком "
        'формате: {"need": "...", "budget": "...", "timeline": "..."}'
    )
    convo = "\n".join(
        f"{'Клиент' if m['role'] == 'user' else 'Бот'}: {m['content']}"
        for m in history
        if m["role"] in ("user", "assistant")
    )
    raw = await _complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": convo},
        ],
        json_mode=True,
        extract=True,
    )
    match = _JSON_RE.search(raw)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}
