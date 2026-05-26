"""Benchmark smoke testovi — verifikacija da BIRD runner i comparator rade.

Smoke level: koristi mock LLM (pre-defined SQL po pitanju), ne treba pravi
OpenAI/Anthropic ključ. Testira:
- rows_equal comparator (multiset i strict order semantika)
- BenchmarkQuestionResult shape
- Run-to-dict serialization

Ne pokreće stvarni BIRD dataset niti SQLite baze — to bi bio integration
test koji zahtjeva ``download_bird.py`` artifakt-e i traje predugo za CI.
"""
