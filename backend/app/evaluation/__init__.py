"""Benchmark evaluacija — Faza 4 (najvažniji istraživački dio diplomskog).

Sadržaj:
- ``runner``       — BenchmarkRunner pokreće set pitanja kroz odabrani eksperiment.
- ``experiments``  — A: samo pitanje; B: +shema; C: +relacije +pravila; D: +retry.
- ``metrics``      — Exact Match, Execution Accuracy, Latency, Security Rejection.
- ``comparators``  — usporedba rezultat-skupova (set equality, sorting tolerant).
- ``bird_loader``  — učitavanje BIRD-Mini dataset-a (pitanje + očekivani SQL).
- ``robustness``   — JOIN/GROUP BY/ambig/SQL-injection test setovi.

Implementacija dolazi u Fazi 4.
"""
