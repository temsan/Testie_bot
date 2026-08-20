# -*- coding: utf-8 -*-
"""
Клиент бесплатного LLM-провайдера OpenRouter (OpenAI-совместимый Chat
Completions API).

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
import os
import re

import httpx

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "liquid/lfm-2.5-2.6b:free"
EXTRACT_MODEL = "liquid/lfm-2.5-2.6b:free"

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


async def _complete(messages: list, model: str, **kwargs) -> str:
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


async def reply(messages: list) -> str:
    """Обычный разговорный ответ клиенту."""
    model = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
    return await _complete(messages, model, temperature=0.5)


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
    model = os.getenv("OPENROUTER_EXTRACT_MODEL", EXTRACT_MODEL)
    raw = await _complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": convo},
        ],
        model,
        temperature=0,
        max_tokens=2500,
        response_format={"type": "json_object"},
    )
    match = _JSON_RE.search(raw)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}
