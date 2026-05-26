"""Cross-cutting concerns: logging, exceptions, timing.

Ovaj paket sadrži module koji nisu vezani za specifičnu domenu aplikacije,
nego su podloga koju koriste svi ostali slojevi:

- ``logging``    — structlog konfiguracija (JSON ili console output).
- ``exceptions`` — custom exception hijerarhija (ValidationError, LLMError…).
- ``timing``     — context manager za mjerenje latencije pojedinih faza.
"""
