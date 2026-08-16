"""連線池。規格書 §4.3：所有資料庫存取經由 FastAPI，單一入口。

刻意不做 repository 抽象層（守則 §1 規則 6）—— 這裡只有連線池本身。
"""

import asyncpg

from app import config

_pool: asyncpg.Pool | None = None


async def connect(dsn: str | None = None) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn or config.DATABASE_URL, min_size=1, max_size=10
        )
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("資料庫連線池尚未建立")
    return _pool


def set_pool(p: asyncpg.Pool | None) -> None:
    """測試 fixture 用（[P01]）。"""
    global _pool
    _pool = p
