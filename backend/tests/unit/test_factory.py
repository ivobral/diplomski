"""Tests for the LLM provider factory.

Single-provider build — only OpenAI. The factory must:
- return ``OpenAIProvider`` for ``"openai"`` (default + named),
- accept a per-call model override (used by the CLI runner),
- raise ``ConfigurationError`` for any other name,
- raise ``ConfigurationError`` when API key / model is missing,
- report the active provider via ``list_configured_providers``.

Settings are monkeypatched so the test doesn't need a real API key.
"""

import pytest

from app.core.exceptions import ConfigurationError
from app.llm import factory
from app.llm.openai_provider import OpenAIProvider


@pytest.fixture
def fake_openai_env(monkeypatch: pytest.MonkeyPatch):
    """Populate OpenAI settings with safe fake values."""

    monkeypatch.setattr(factory, "OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(factory, "OPENAI_MODEL", "gpt-fake")
    monkeypatch.setattr(factory, "OPENAI_BASE_URL", "")
    # The constructor also reads through app.config — patch there too.
    from app import config

    monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(config.settings, "OPENAI_MODEL", "gpt-fake")
    monkeypatch.setattr(config.settings, "OPENAI_BASE_URL", "")
    monkeypatch.setattr(config.settings, "OPENAI_REASONING_EFFORT", "")


class TestProviderTypes:
    def test_openai_default(self, fake_openai_env: None) -> None:
        provider = factory.create_llm_provider()
        assert isinstance(provider, OpenAIProvider)

    def test_openai_by_name(self, fake_openai_env: None) -> None:
        provider = factory.create_llm_provider_for("openai")
        assert isinstance(provider, OpenAIProvider)

    def test_unknown_provider_raises(self, fake_openai_env: None) -> None:
        with pytest.raises(ConfigurationError, match="Unknown"):
            factory.create_llm_provider_for("anthropic")


class TestModelOverride:
    def test_override_takes_effect(self, fake_openai_env: None) -> None:
        provider = factory.create_llm_provider_for("openai", model="gpt-5-mini")
        assert provider._model == "gpt-5-mini"  # noqa: SLF001 — testing internal state

    def test_default_from_env(self, fake_openai_env: None) -> None:
        provider = factory.create_llm_provider_for("openai")
        assert provider._model == "gpt-fake"  # noqa: SLF001


class TestMissingConfig:
    def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config

        monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "")
        monkeypatch.setattr(config.settings, "OPENAI_MODEL", "gpt-fake")
        with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
            factory.create_llm_provider_for("openai")

    def test_missing_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config

        monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "sk-fake")
        monkeypatch.setattr(config.settings, "OPENAI_MODEL", "")
        with pytest.raises(ConfigurationError, match="OPENAI_MODEL"):
            factory.create_llm_provider_for("openai")


class TestListConfigured:
    def test_active_when_configured(self, fake_openai_env: None) -> None:
        result = factory.list_configured_providers()
        assert len(result) == 1
        assert result[0]["name"] == "openai"
        assert result[0]["model"] == "gpt-fake"

    def test_empty_when_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(factory, "OPENAI_API_KEY", "")
        monkeypatch.setattr(factory, "OPENAI_MODEL", "")
        assert factory.list_configured_providers() == []
