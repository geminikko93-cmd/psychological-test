"""경로와 로컬 설정 저장소.

- 일반 설정: %APPDATA%\\JPShortsStudio\\settings.json
- 비밀값(API 키, 추가 헤더 값): %APPDATA%\\JPShortsStudio\\secrets.dat
  Windows에서는 DPAPI(CryptProtectData)로 현재 Windows 사용자 계정에 묶어 암호화한다.
- 프로젝트 데이터: 문서\\JPShortsStudio\\projects (JPSS_DATA_DIR 환경변수로 변경 가능)

비밀값은 프런트엔드로 절대 내려보내지 않는다(설정 여부만 알려준다).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

APP_NAME = "JPShortsStudio"


def _appdata_dir() -> Path:
    override = os.environ.get("JPSS_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def _data_dir() -> Path:
    override = os.environ.get("JPSS_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / "Documents" / APP_NAME


CONFIG_DIR = _appdata_dir()
DATA_DIR = _data_dir()
PROJECTS_DIR = DATA_DIR / "projects"
MODELS_DIR = DATA_DIR / "models"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
SECRETS_FILE = CONFIG_DIR / "secrets.dat"

DEFAULT_SETTINGS: dict[str, Any] = {
    "ai": {
        # 사용자가 직접 입력해야 하는 값은 비워 둔다(임의 기본 주소/모델 없음).
        "base_url": "",
        "path": "/v1/messages",
        "auth": "x-api-key",  # x-api-key | bearer | custom | none
        "custom_header_name": "",
        "anthropic_version": "2023-06-01",
        "send_version_header": True,
        "model": "",
        "max_tokens": 8000,
        "timeout_s": 180,
        "stream": False,
        "extra_body_json": "",
        "price_input_per_mtok": None,
        "price_output_per_mtok": None,
        "price_currency": "USD",
    },
    "subtitle": {
        "max_line_chars": 14,
        "max_lines": 2,
        "min_cue_s": 0.8,
        "max_cps": 9.0,
        "fill_gaps_under_s": 0.6,
        "srt_bom": False,
    },
    "whisper": {
        "model_size": "small",
    },
}

SECRET_KEYS = ("ai_api_key", "ai_extra_headers", "pexels_key", "pixabay_key")

_lock = threading.Lock()


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, PROJECTS_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _deep_merge(base: dict, extra: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    with _lock:
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        else:
            data = {}
        return _deep_merge(DEFAULT_SETTINGS, data)


def save_settings(settings: dict) -> dict:
    merged = _deep_merge(DEFAULT_SETTINGS, settings)
    # 설정 파일에 비밀값이 섞여 들어오지 않도록 방어
    for k in SECRET_KEYS:
        merged.pop(k, None)
        merged.get("ai", {}).pop(k, None)
    merged.get("ai", {}).pop("api_key", None)
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    return merged


# ---------------------------------------------------------------- DPAPI
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    def _blob(data: bytes) -> _BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _protect(data: bytes) -> bytes:
        inp = _blob(data)
        out = _BLOB()
        if not _crypt32.CryptProtectData(ctypes.byref(inp), "JPSS", None, None, None, 0x1, ctypes.byref(out)):
            raise OSError("DPAPI 암호화 실패")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)

    def _unprotect(data: bytes) -> bytes:
        inp = _blob(data)
        out = _BLOB()
        if not _crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out)):
            raise OSError("DPAPI 복호화 실패")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)

    _SECRET_PREFIX = b"DPAPI1:"
else:  # 개발용 대체(Windows 외 환경). 암호화가 아니라 단순 인코딩이므로 표시한다.
    def _protect(data: bytes) -> bytes:
        return data

    def _unprotect(data: bytes) -> bytes:
        return data

    _SECRET_PREFIX = b"PLAIN1:"


def load_secrets() -> dict:
    with _lock:
        if not SECRETS_FILE.exists():
            return {}
        raw = SECRETS_FILE.read_bytes()
        try:
            if raw.startswith(b"DPAPI1:"):
                plain = _unprotect(base64.b64decode(raw[7:]))
            elif raw.startswith(b"PLAIN1:"):
                plain = base64.b64decode(raw[7:])
            else:
                return {}
            data = json.loads(plain.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def save_secrets(updates: dict) -> None:
    """updates: {키: 값}. 값이 None이면 유지, ""이면 삭제."""
    current = load_secrets()
    for k, v in updates.items():
        if k not in SECRET_KEYS or v is None:
            continue
        if v == "" or v == {}:
            current.pop(k, None)
        else:
            current[k] = v
    payload = json.dumps(current, ensure_ascii=False).encode("utf-8")
    blob = _SECRET_PREFIX + base64.b64encode(_protect(payload))
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SECRETS_FILE.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, SECRETS_FILE)


def secret_status() -> dict:
    s = load_secrets()
    headers = s.get("ai_extra_headers") or {}
    return {
        "ai_api_key": bool(s.get("ai_api_key")),
        "ai_extra_header_names": sorted(headers.keys()) if isinstance(headers, dict) else [],
        "pexels_key": bool(s.get("pexels_key")),
        "pixabay_key": bool(s.get("pixabay_key")),
        "encrypted": _SECRET_PREFIX == b"DPAPI1:",
    }


def all_secret_values() -> list[str]:
    """내보내기·저장 검사용: 현재 저장된 모든 비밀 문자열."""
    s = load_secrets()
    vals: list[str] = []
    for k, v in s.items():
        if isinstance(v, str) and v:
            vals.append(v)
        elif isinstance(v, dict):
            vals.extend(str(x) for x in v.values() if x)
    return [v for v in vals if len(v) >= 6]
