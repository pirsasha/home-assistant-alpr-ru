from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("ALPR_RU_DATA_DIR", "/data"))
SETTINGS_PATH = DATA_DIR / "settings.json"
LAST_SENT_PATH = DATA_DIR / "last_sent.jpg"
LAST_RESULT_PATH = DATA_DIR / "last_result.jpg"
HISTORY_DIR = DATA_DIR / "history"
HISTORY_LIMIT = 10
DB_PATH = DATA_DIR / "alpr_ru.db"
APP_VERSION = os.environ.get("ALPR_RU_VERSION", "0.1.9")

DEFAULT_SETTINGS: dict[str, Any] = {
    "api_url": "https://api-alpr.pirogovx.ru",
    "api_key": "",
    "camera_entity": "",
    "trigger_mode": "none",
    "trigger_entity": "",
    "dahua_url": "",
    "dahua_username": "admin",
    "dahua_password": "",
    "dahua_motion_cooldown": 10,
    "plate_type": "auto",
    "gate_entity": "",
    "min_confidence": 0.85,
    "gate_cooldown": 30,
}


def load_settings() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    values = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                values.update(saved)
                if "trigger_mode" not in saved and saved.get("trigger_entity"):
                    values["trigger_mode"] = "ha"
        except (OSError, json.JSONDecodeError):
            pass
    return values


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = dict(DEFAULT_SETTINGS)
    normalized.update(values)
    normalized["api_url"] = str(normalized.get("api_url") or "").strip().rstrip("/")
    normalized["api_key"] = str(normalized.get("api_key") or "").strip()
    normalized["camera_entity"] = str(normalized.get("camera_entity") or "").strip()
    normalized["trigger_mode"] = str(normalized.get("trigger_mode") or "none").strip()
    normalized["trigger_entity"] = str(normalized.get("trigger_entity") or "").strip()
    normalized["dahua_url"] = str(normalized.get("dahua_url") or "").strip().rstrip("/")
    normalized["dahua_username"] = str(normalized.get("dahua_username") or "").strip()
    normalized["dahua_password"] = str(normalized.get("dahua_password") or "")
    normalized["dahua_motion_cooldown"] = max(
        0, int(normalized.get("dahua_motion_cooldown", 10))
    )
    normalized["plate_type"] = str(normalized.get("plate_type") or "auto").strip()
    normalized["gate_entity"] = str(normalized.get("gate_entity") or "").strip()
    normalized["min_confidence"] = max(
        0.0, min(1.0, float(normalized.get("min_confidence", 0.85)))
    )
    normalized["gate_cooldown"] = max(0, int(normalized.get("gate_cooldown", 30)))
    SETTINGS_PATH.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return normalized
