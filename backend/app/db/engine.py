"""SQLAlchemy async engine"""

from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from app.config import settings


_main_engine: AsyncEngine | None = None
_readonly_engine: AsyncEngine | None = None


def get_main_engine() -> AsyncEngine:
    global _main_engine
    if _main_engine is None:
        _main_engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
    return _main_engine


def get_readonly_engine() -> AsyncEngine:
    global _readonly_engine
    if _readonly_engine is None:
        _readonly_engine = create_async_engine(settings.READONLY_DATABASE_URL, echo=False, pool_pre_ping=True)
    return _readonly_engine
    

async def dispose_engines() -> None:
    global _main_engine, _readonly_engine
    if _main_engine is not None:
        await _main_engine.dispose()
        _main_engine = None
    if _readonly_engine is not None:
        await _readonly_engine.dispose()
        _readonly_engine = None
