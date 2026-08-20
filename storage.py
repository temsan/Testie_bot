# -*- coding: utf-8 -*-
"""
Хранилище конфигурации ниши/квалификации.

Источник значений по умолчанию — config.py (NICHE + QUALIFICATION_RULES).
Если рядом лежит niche_config.json (создаётся мастером в /admin), его
значения перекрывают дефолтные — так изменения из админ-мастера
применяются сразу, без правки кода и без рестарта бота.
"""

import difflib
import json
from pathlib import Path

from config import NICHE, QUALIFICATION_RULES

CONFIG_PATH = Path(__file__).parent / "niche_config.json"
LEADS_PATH = Path(__file__).parent / "leads.json"


def _defaults() -> dict:
    cfg = dict(NICHE)
    cfg["budget_scores"] = dict(QUALIFICATION_RULES["budget_scores"])
    cfg["timeline_scores"] = dict(QUALIFICATION_RULES["timeline_scores"])
    cfg["QUALIFY_THRESHOLD"] = QUALIFICATION_RULES["QUALIFY_THRESHOLD"]
    return cfg


def get_config() -> dict:
    cfg = _defaults()
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def reset_config() -> None:
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def match_option(text: str, options: list) -> str:
    """Сопоставляет свободный текст лида с ближайшим вариантом из списка кнопок.

    Точное совпадение (с учётом регистра/пробелов) — как раньше. Если лид
    написал что-то своими словами вместо нажатия кнопки, подбирается
    ближайший по написанию вариант; при слишком слабом совпадении
    возвращается исходный текст (даст 0 баллов при скоринге, как и раньше).
    """
    text = text.strip()
    for opt in options:
        if opt.strip().lower() == text.lower():
            return opt
    close = difflib.get_close_matches(text, options, n=1, cutoff=0.4)
    return close[0] if close else text


def append_lead(lead: dict) -> None:
    leads = []
    if LEADS_PATH.exists():
        try:
            leads = json.loads(LEADS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            leads = []
    leads.append(lead)
    LEADS_PATH.write_text(json.dumps(leads, ensure_ascii=False, indent=2), encoding="utf-8")


def get_leads(limit: int = 10) -> list:
    if not LEADS_PATH.exists():
        return []
    try:
        leads = json.loads(LEADS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return leads[-limit:][::-1]
