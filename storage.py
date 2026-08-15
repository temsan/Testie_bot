# -*- coding: utf-8 -*-
"""
Хранилище конфигурации ниши/квалификации.

Источник значений по умолчанию — config.py (NICHE + QUALIFICATION_RULES).
Если рядом лежит niche_config.json (создаётся мастером в /admin), его
значения перекрывают дефолтные — так изменения из админ-мастера
применяются сразу, без правки кода и без рестарта бота.
"""

import json
from pathlib import Path

from config import NICHE, QUALIFICATION_RULES

CONFIG_PATH = Path(__file__).parent / "niche_config.json"


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
