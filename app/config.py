"""Runtime configuration for the 4yi-cad dedicated app.

The 4yi platform injects the LLM gateway contract as environment variables at
install time (OPENAI_BASE_URL -> ${origin}/api/v1, OPENAI_API_KEY -> per-install
xclaw-bsl-* token, TEXT_MODEL -> resolved model). We read ONLY these for LLM
access and fail fast if any is missing — there is deliberately no fallback to
api.openai.com, so a misconfigured install cannot silently bill an external
provider or leak traffic off-platform.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when required runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    openai_base_url: str
    openai_api_key: str
    text_model: str
    port: int


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ConfigError(f"Missing required environment variable: {name}")
    return value.strip()


def load_config() -> Config:
    openai_base_url = _require("OPENAI_BASE_URL")
    openai_api_key = _require("OPENAI_API_KEY")
    text_model = _require("TEXT_MODEL")

    raw_port = os.environ.get("PORT", "8080").strip() or "8080"
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ConfigError(f"PORT must be an integer, got {raw_port!r}") from exc

    return Config(
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
        text_model=text_model,
        port=port,
    )
