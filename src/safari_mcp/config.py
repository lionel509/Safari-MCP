"""Settings, resolved from the environment first and a config file second.

Nothing here is secret — unlike reel-analyzer there is no API key, because the
gate model runs locally. The file exists so the denylist can be edited without
touching the host's MCP registration.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "safari-mcp" / "config.json"

# Sites where a stray click is expensive or irreversible. These block every
# write — navigate, click, fill, run_js — and the gate model cannot overrule
# them. Reads are always allowed, everywhere.
DEFAULT_DENYLIST: tuple[str, ...] = (
    r"^https?://mail\.google\.com/",
    r"^https?://[^/]*\.?outlook\.(com|live\.com|office\.com)/",
    r"^https?://[^/]*\.?mail\.proton\.me/",
    # auth and account surfaces on any host
    r"^https?://[^/]*/(auth|signin|sign-in|login|log-in|account/(login|password))",
    r"^https?://accounts\.google\.com/",
    # money
    r"^https?://[^/]*\.?(chase|jpmorgan|bankofamerica|wellsfargo|citi|capitalone)\.com/",
    r"^https?://[^/]*\.?(amex|americanexpress|discover)\.com/",
    r"^https?://[^/]*\.?(schwab|fidelity|vanguard|robinhood|coinbase)\.com/",
    # anything that looks like a checkout confirmation
    r"^https?://[^/]*/(checkout/(review|place|confirm)|placeorder)",
)


class ConfigError(RuntimeError):
    """Configuration is missing or unusable. Surfaced to the caller verbatim."""


@dataclass(frozen=True)
class Settings:
    denylist: tuple[re.Pattern[str], ...]
    guard_enabled: bool
    guard_model: str
    ollama_host: str
    guard_timeout: float
    load_timeout: float
    raw_denylist: tuple[str, ...] = field(default=())


def _file_values() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"{CONFIG_PATH} could not be read as JSON ({exc}) — fix or delete it"
        ) from exc


def _bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load() -> Settings:
    """Environment wins, then the config file, then the defaults here."""
    data = _file_values()

    def pick(env: str, key: str, default):
        if (v := os.environ.get(env)) is not None:
            return v
        return data.get(key, default)

    patterns = data.get("denylist")
    if patterns is None:
        patterns = list(DEFAULT_DENYLIST)
    if not isinstance(patterns, list) or any(not isinstance(p, str) for p in patterns):
        raise ConfigError(
            f'"denylist" in {CONFIG_PATH} must be a list of regex strings'
        )
    if extra := os.environ.get("SAFARI_MCP_DENY_EXTRA"):
        patterns = patterns + [p for p in extra.split(",") if p.strip()]

    compiled = []
    for p in patterns:
        try:
            compiled.append(re.compile(p, re.IGNORECASE))
        except re.error as exc:
            raise ConfigError(f"denylist entry {p!r} is not a valid regex: {exc}") from exc

    try:
        guard_timeout = float(pick("SAFARI_MCP_GUARD_TIMEOUT", "guard_timeout", 20.0))
        load_timeout = float(pick("SAFARI_MCP_LOAD_TIMEOUT", "load_timeout", 20.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"timeout settings must be numbers: {exc}") from exc

    return Settings(
        denylist=tuple(compiled),
        raw_denylist=tuple(patterns),
        guard_enabled=_bool(pick("SAFARI_MCP_GUARD", "guard_enabled", None), True),
        guard_model=str(pick("SAFARI_MCP_GUARD_MODEL", "guard_model", "qwen3.5:2b")),
        ollama_host=str(
            pick("SAFARI_MCP_OLLAMA_HOST", "ollama_host", "http://127.0.0.1:11434")
        ).rstrip("/"),
        guard_timeout=guard_timeout,
        load_timeout=load_timeout,
    )
