"""Local storage for cloud API keys (only the Gemini key so far).

The key lives in <DATA_DIR>/secrets.json with 0600 permissions (readable only by this macOS
user), or comes from the GEMINI_API_KEY environment variable (takes precedence). It is never logged and never returned to the browser — only whether one is set
and its last 4 characters.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from mytts import config

_ENV_VARS = ("GEMINI_API_KEY",)  # not GOOGLE_API_KEY: an unrelated key must not override the saved one


def _path() -> Path:
    return config.DATA_DIR / "secrets.json"


def _read() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, p)
    os.chmod(p, 0o600)


def gemini_key() -> Optional[str]:
    for var in _ENV_VARS:
        if os.environ.get(var):
            return os.environ[var].strip()
    return _read().get("gemini_api_key") or None


def gemini_key_status() -> dict:
    key = gemini_key()
    source = next((v for v in _ENV_VARS if os.environ.get(v)), "file" if key else None)
    return {"configured": bool(key), "last4": key[-4:] if key else None, "source": source}


def set_gemini_key(key: str) -> None:
    data = _read()
    data["gemini_api_key"] = key.strip()
    _write(data)


def delete_gemini_key() -> None:
    data = _read()
    data.pop("gemini_api_key", None)
    _write(data)
