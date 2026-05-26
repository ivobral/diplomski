"""HTTP API layer — FastAPI routeri.

Svaki ``.py`` file u ovom paketu definira jedan APIRouter koji se registrira
u ``app.main``. Routeri su tanki — sva business logika delegirana je u
``app/services``. Time se postiže:

- jasna podjela odgovornosti (API = transport, services = orkestracija),
- lakša testabilnost (servisi se mogu testirati bez HTTP sloja),
- dependency injection kroz FastAPI ``Depends()`` ostaje pregledan.
"""
