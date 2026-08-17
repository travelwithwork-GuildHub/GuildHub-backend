"""測試共用 fixture。

軌 R 的測試需要一個真的 server —— fake_client 走真實 WebSocket，用 mock 或
ASGI 測試客戶端就驗不到節流與封包量（任務表 [R17] [R18]：必須實際計數封包）。
"""

import socket
import threading
import time
from pathlib import Path

import asyncpg
import httpx
import pytest
import uvicorn

from app import config, db as db_module
from app.main import app

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def db_unavailable() -> str | None:
    """只探測一次資料庫在不在，回傳失敗原因（在的話回 None）。

    沒有這層的話，[D01] 完成前每個需要資料庫的測試都要各自等一次連線逾時 ——
    四十幾個測試就是好幾分鐘，慢到大家會開始習慣性跳過整個測試套件。
    """
    import asyncio

    async def probe() -> str | None:
        try:
            conn = await asyncpg.connect(config.DATABASE_URL, timeout=3)
        except Exception as exc:  # noqa: BLE001
            # 一定要帶上型別名稱：連線逾時拋的是 TimeoutError，而它的
            # str() 是空字串 —— 直接回 str(exc) 會得到 ""，被當成 falsy，
            # 於是「資料庫不在」反而被讀成「資料庫在」。
            return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        await conn.close()
        return None

    return asyncio.run(probe())


@pytest.fixture
async def db(db_unavailable):
    """[P01] 每個測試拿到一份乾淨 schema。

    刻意不提供 SQLite 後備：SQLite 的約束行為與 PostgreSQL 不同（enum、
    text[]、deferrable、check 的細節都不一樣），用它會讓 §4.1 的四個不變式
    假綠 —— 那正是這些測試唯一要證明的東西（任務表 [P01]）。

    沒有可用的 PostgreSQL 時直接 skip 並說明原因，不會安靜地通過。
    """
    if db_unavailable:
        pytest.skip(f"沒有可用的 PostgreSQL（[D01] 尚未完成）：{db_unavailable}")

    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=10)

    schema = (SQL_DIR / "001_schema.sql").read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        # 整個 schema 砍掉重建，比逐表 truncate 更難留下殘留狀態
        await conn.execute("drop schema public cascade; create schema public;")
        await conn.execute(schema)

    db_module.set_pool(pool)
    try:
        yield pool
    finally:
        db_module.set_pool(None)
        await pool.close()


@pytest.fixture
async def api(db):
    """[P02] 對 FastAPI app 發請求，不啟真 server。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def login(db):
    """產生多個各自登入的 client。

    每個 client 有獨立的 cookie jar，所以是真的不同 session —— [P38] 的
    座位競爭要靠這個，同一個 session 打兩次不算兩個人在搶。
    """
    opened: list[httpx.AsyncClient] = []

    async def make(nickname: str) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        opened.append(client)
        response = await client.post("/api/login", json={"nickname": nickname})
        assert response.status_code == 200, response.text
        client.user_id = response.json()["id"]
        return client

    yield make

    for client in opened:
        await client.aclose()


def _boot(**kwargs) -> tuple[int, uvicorn.Server, threading.Thread]:
    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on", **kwargs
    )
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("測試用 server 起不來")
        time.sleep(0.05)
    return port, srv, thread


@pytest.fixture(scope="session")
def server_fast_ping() -> int:
    """[R25] 用。同一個 app，但把 WS ping 壓到 1 秒，讓逾時偵測可以在測試裡驗。

    正式環境用 uvicorn 預設的 20 秒（見 run.sh）—— 機制相同，只有時間常數不同。
    """
    port, srv, thread = _boot(ws_ping_interval=1.0, ws_ping_timeout=1.0)
    yield port
    srv.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="session")
def server() -> int:
    """在背景執行緒起一個真的 uvicorn，回傳 port。"""
    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("測試用 server 起不來")
        time.sleep(0.05)

    yield port

    srv.should_exit = True
    thread.join(timeout=10)
