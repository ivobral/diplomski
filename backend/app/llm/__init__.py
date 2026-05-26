"""LLM provider abstraction.

Sustav podržava tri providera: Anthropic Claude, OpenAI GPT, lokalni Ollama.
Razdvojeni su iza zajedničkog interface-a ``BaseLLMProvider`` (Faza 2),
a ``factory.py`` bira konkretnog providera na temelju ``settings.LLM_PROVIDER``.

Filozofija: **bez wrapper-frameworka (LangChain, LlamaIndex)**. Izravni SDK
pozivi i ručno kontruirani prompti — diplomski rad mora demonstrirati
razumijevanje, ne tuđu apstrakciju.

Pod-paketi:
- ``prompts/`` — PromptBuilder i strategije (A/B/C/D).
"""
