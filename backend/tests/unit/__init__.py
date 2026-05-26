"""Unit testovi pojedinačnih klasa/funkcija s mock ovisnostima.

Pokriva:
- ``extract_sql`` helper (parser za LLM response)
- ``PromptBuilder`` (strategije A/B/C/D)
- ``factory`` (LLM provider odabir + model override)
- ``FewShotRetriever`` (TF-IDF semantička pretraga)
- ``value_mapper`` (entity → DB value lookup)
- ``result_judge`` (LLM-as-judge JSON parser)
"""
