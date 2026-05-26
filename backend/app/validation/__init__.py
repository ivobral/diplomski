"""SQL validation pipeline — kritičan sigurnosni sloj.

Pipeline koji svaki LLM-generirani SQL mora proći prije izvršavanja:

1. **AST parsing** (``ast_checks``)       — sqlglot mora parsati SQL kao
   jedan SELECT statement.
2. **Safety checks** (``safety_checks``)  — blok DDL/DML, multi-statement.
3. **Semantic checks** (``semantic_checks``) — tablice i kolone moraju
   postojati u stvarnoj shemi.
4. **Enforcers** (``enforcers``)          — auto-LIMIT ako nedostaje.

Dizajn-pravilo: validacija se NE oslanja na regex ni na prompt — koristi
AST analizu jer je to jedini robusan način uhvatiti sve obfuscation varijante
(komentari, novi redovi, case manipulacija). Defense-in-depth uz read-only
DB usera čini sustav otpornim na pokušaje obilaska.

Implementacija dolazi u Fazi 2.
"""
