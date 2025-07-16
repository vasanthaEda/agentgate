"""Async SQLAlchemy engine/session plumbing.

Works against SQLite (dev/tests) and Postgres (prod) via the same async
engine interface -- the only thing that differs is the connection URL and
a couple of dialect-specific connect_args.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(database_url: str):
    connect_args: dict = {}
    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in database_url:
            # Keep a single shared in-memory DB alive for the life of the process/test.
            kwargs["poolclass"] = StaticPool
    return create_async_engine(database_url, connect_args=connect_args, **kwargs)


engine = _make_engine(settings.database_url)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def init_db(bind_engine=None) -> None:
    """Create all tables. Used at startup for SQLite/dev and by the test suite."""
    target = bind_engine or engine
    async with target.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
