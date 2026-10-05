"""Settings, paths and environment handling. Secrets are never printed or logged."""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
LOGO_DIR = ROOT / "organization_logos"
REPORTS_DIR = ROOT / "reports"

_ENV_LOADED = False


def load_env() -> None:
    """Load ``.env`` once (python-dotenv understands the optional ``export`` prefix)."""
    global _ENV_LOADED
    if not _ENV_LOADED:
        load_dotenv(ROOT / ".env", override=False)
        _ENV_LOADED = True


def data_dir() -> Path:
    d = Path(os.environ.get("NSW_DATA_DIR", ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return Path(os.environ.get("NSW_DB_PATH", data_dir() / "nsw.db"))


def cache_path() -> Path:
    return Path(os.environ.get("NSW_LLM_CACHE", data_dir() / "llm_cache.sqlite"))


def offline_forced() -> bool:
    return os.environ.get("NSW_OFFLINE", "").strip().lower() in {"1", "true", "yes"}


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=None)
def yaml_config(name: str) -> Any:
    """Load ``config/<name>.yaml`` (cached)."""
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def env_first(*names: str) -> tuple[str | None, str | None]:
    """Return (value, name) of the first non-empty environment variable among ``names``."""
    load_env()
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v, n
    return None, None


# ---------------------------------------------------------------- secret redaction
_KEY_PATTERNS = [
    re.compile(r"ABSK[A-Za-z0-9+/=_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9+/=_\-\.]{16,}"),
]


def _secret_values() -> list[str]:
    load_env()
    out = []
    for n in ("OPENAI_API_KEY", "AWS_BEARER_TOKEN_BEDROCK", "OPENAI_PROJECT_ID"):
        v = os.environ.get(n, "")
        if len(v) >= 8:
            out.append(v)
    return out


def redact(text: Any) -> str:
    """Replace any known secret (and key-shaped strings) with ``***``."""
    s = str(text)
    for v in _secret_values():
        s = s.replace(v, "***")
    for p in _KEY_PATTERNS:
        s = p.sub("***", s)
    return s


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # pragma: no cover - never let logging fail
            pass
        return True


def get_logger(name: str) -> logging.Logger:
    for noisy in ("httpx", "httpx2", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in log.filters):
        log.addFilter(RedactingFilter())
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        for h in logging.getLogger().handlers:
            h.addFilter(RedactingFilter())
    return log


def startup_banner() -> str:
    """The only startup line about the LLM: key present/absent, base URL host, model id."""
    from urllib.parse import urlparse

    from nsw_sim.llm.client import resolve_llm_settings

    cfg = resolve_llm_settings()
    host = urlparse(cfg.base_url).netloc if cfg.base_url else "none"
    return f"LLM key: {'present' if cfg.api_key else 'absent'} · base URL host: {host} · model id: data={cfg.model_data} chat={cfg.model_chat}"
