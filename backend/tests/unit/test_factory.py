"""Testovi za LLM provider factory.

Factory mora:
- Vratiti pravu klasu za zadano ime (anthropic/openai/ollama/gemini)
- Prihvatiti per-call model override (za CLI runner)
- Baciti ``ConfigurationError`` za nepoznata imena
- ``list_configured_providers()`` mora vratiti samo provide-e s popunjenom konfiguracijom

Test koristi monkeypatch na ``settings`` da ne treba prave API ključeve.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import ConfigurationError
from app.llm import factory
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.gemini_provider import GeminiProvider
from app.llm.ollama_provider import OllamaProvider
from app.llm.openai_provider import OpenAIProvider


@pytest.fixture
def _fake_settings(monkeypatch: pytest.MonkeyPatch):
    """Postavi sve potrebne env varijable da svaki provider može inicijalizirati.

    Vrijednosti su fake — testovi ne pozivaju ``generate()`` pa pravi API
    ključ nije potreban.
    """

    from app import config

    monkeypatch.setattr(config.settings, "ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr(config.settings, "ANTHROPIC_MODEL", "claude-fake")
    monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(config.settings, "OPENAI_MODEL", "gpt-fake")
    monkeypatch.setattr(config.settings, "OPENAI_BASE_URL", "")
    monkeypatch.setattr(config.settings, "OPENAI_REASONING_EFFORT", "")
    monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "g-fake")
    monkeypatch.setattr(config.settings, "GEMINI_MODEL", "gemini-fake")
    monkeypatch.setattr(config.settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(config.settings, "OLLAMA_MODEL", "ollama-fake")


class TestProviderTypes:
    """create_llm_provider_for vraća odgovarajuću klasu za svako ime."""

    def test_anthropic(self, _fake_settings: None) -> None:
        p = factory.create_llm_provider_for("anthropic")
        assert isinstance(p, AnthropicProvider)

    def test_openai(self, _fake_settings: None) -> None:
        p = factory.create_llm_provider_for("openai")
        assert isinstance(p, OpenAIProvider)

    def test_ollama(self, _fake_settings: None) -> None:
        p = factory.create_llm_provider_for("ollama")
        assert isinstance(p, OllamaProvider)

    def test_gemini(self, _fake_settings: None) -> None:
        p = factory.create_llm_provider_for("gemini")
        assert isinstance(p, GeminiProvider)

    def test_unknown_provider_raises(self, _fake_settings: None) -> None:
        with pytest.raises(ConfigurationError, match="Nepoznat"):
            factory.create_llm_provider_for("magic-llm")


class TestModelOverride:
    """Per-call model override nadjača env varijablu, bez restarta procesa."""

    def test_openai_override(self, _fake_settings: None) -> None:
        p = factory.create_llm_provider_for("openai", model="gpt-5-mini")
        assert p._model == "gpt-5-mini"  # noqa: SLF001 — testiramo internu

    def test_openai_default_from_env(self, _fake_settings: None) -> None:
        p = factory.create_llm_provider_for("openai")
        assert p._model == "gpt-fake"  # iz _fake_settings env varijable

    def test_anthropic_override(self, _fake_settings: None) -> None:
        p = factory.create_llm_provider_for("anthropic", model="claude-opus-99")
        assert p._model == "claude-opus-99"  # noqa: SLF001

    def test_gemini_override(self, _fake_settings: None) -> None:
        p = factory.create_llm_provider_for("gemini", model="gemini-3-pro")
        assert p._model == "gemini-3-pro"  # noqa: SLF001

    def test_ollama_override(self, _fake_settings: None) -> None:
        p = factory.create_llm_provider_for("ollama", model="llama3:70b")
        assert p._model == "llama3:70b"  # noqa: SLF001


class TestMissingConfig:
    """Provider bez API ključa ili modela baca ConfigurationError."""

    def test_openai_no_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config

        monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "")
        monkeypatch.setattr(config.settings, "OPENAI_MODEL", "gpt-fake")
        with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
            factory.create_llm_provider_for("openai")

    def test_openai_no_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config

        monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "sk-fake")
        monkeypatch.setattr(config.settings, "OPENAI_MODEL", "")
        with pytest.raises(ConfigurationError, match="OPENAI_MODEL"):
            factory.create_llm_provider_for("openai")


class TestListConfigured:
    """list_configured_providers vraća samo one s popunjenom konfiguracijom."""

    def test_all_configured(self, _fake_settings: None) -> None:
        result = factory.list_configured_providers()
        names = {p["name"] for p in result}
        assert names == {"anthropic", "openai", "ollama", "gemini"}

    def test_only_partial_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ako samo OpenAI ima oba ključa i model, samo on je listed."""

        from app import config

        # Sve isključeno osim OpenAI
        monkeypatch.setattr(config.settings, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(config.settings, "ANTHROPIC_MODEL", "")
        monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "sk-fake")
        monkeypatch.setattr(config.settings, "OPENAI_MODEL", "gpt-fake")
        monkeypatch.setattr(config.settings, "OPENAI_BASE_URL", "")
        monkeypatch.setattr(config.settings, "GEMINI_API_KEY", "")
        monkeypatch.setattr(config.settings, "GEMINI_MODEL", "")
        monkeypatch.setattr(config.settings, "OLLAMA_MODEL", "")
        result = factory.list_configured_providers()
        assert len(result) == 1
        assert result[0]["name"] == "openai"
