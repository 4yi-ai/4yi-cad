import pytest

from app.config import Config, ConfigError, load_config

_REQUIRED = {
    "OPENAI_BASE_URL": "https://platform.example/api/v1",
    "OPENAI_API_KEY": "xclaw-bsl-testtoken",
    "TEXT_MODEL": "anthropic.claude-sonnet-4-6",
}


def test_load_config_reads_gateway_contract(monkeypatch):
    for k, v in _REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PORT", "9001")

    cfg = load_config()

    assert isinstance(cfg, Config)
    assert cfg.openai_base_url == "https://platform.example/api/v1"
    assert cfg.openai_api_key == "xclaw-bsl-testtoken"
    assert cfg.text_model == "anthropic.claude-sonnet-4-6"
    assert cfg.port == 9001


def test_port_defaults_to_8080_when_unset(monkeypatch):
    for k, v in _REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("PORT", raising=False)

    cfg = load_config()

    assert cfg.port == 8080


@pytest.mark.parametrize("missing", list(_REQUIRED.keys()))
def test_missing_required_env_fails_fast(monkeypatch, missing):
    for k, v in _REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ConfigError) as exc:
        load_config()

    assert missing in str(exc.value)


def test_accepts_openai_api_base_alias(monkeypatch):
    # The 4yi import scanner may inject the gateway URL as OPENAI_API_BASE (the
    # older OpenAI env name) instead of OPENAI_BASE_URL. Accept either.
    monkeypatch.setenv("OPENAI_API_KEY", "xclaw-bsl-testtoken")
    monkeypatch.setenv("TEXT_MODEL", "anthropic.claude-sonnet-4-6")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_BASE", "https://platform.example/api/v1")

    cfg = load_config()

    assert cfg.openai_base_url == "https://platform.example/api/v1"


def test_missing_both_base_url_names_fails_fast(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "xclaw-bsl-testtoken")
    monkeypatch.setenv("TEXT_MODEL", "anthropic.claude-sonnet-4-6")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)

    with pytest.raises(ConfigError):
        load_config()


def test_no_silent_fallback_to_openai_dot_com(monkeypatch):
    # If the gateway base URL is missing we must fail, never default to api.openai.com.
    for k, v in _REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with pytest.raises(ConfigError):
        load_config()
