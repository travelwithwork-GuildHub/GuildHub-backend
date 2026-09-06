"""測試共用 fixture。

軌 R 的測試需要一個真的 server —— fake_client 走真實 WebSocket，用 mock 或
ASGI 測試客戶端就驗不到節流與封包量（任務表 [R17] [R18]：必須實際計數封包）。

測試連到哪個資料庫由 `TEST_DATABASE_URL` 決定，見下方〈為什麼不吃
DATABASE_URL〉。沒設就整批 skip —— **不會退回 `DATABASE_URL`**。
"""

import os
import socket
import threading
import time
from pathlib import Path

import asyncpg
import httpx
import pytest
import uvicorn
from dotenv import dotenv_values

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# ─────────────────────────────────────────────────────────────────────────
# 測試連哪個庫
#
# 下面的 `db` fixture 對它連到的那個庫下 `drop schema public cascade`。
# 這是對的 —— 逐表 truncate 會留下殘留狀態。錯的是拿它去砍開發用的那一個庫：
# 跑一次 pytest，seed 資料就沒了，而且**測試全綠**，沒有任何東西會提醒你。
#
# 位址由獨立的 `TEST_DATABASE_URL` 決定，沒設就 skip —— **不退回
# `DATABASE_URL`**。退回等於把地雷原封不動留在原地，只是多了一層看起來
# 處理過的樣子。
#
#   docker exec guildhub-db psql -U guildhub -d postgres -c "create database guildhub_test owner guildhub;"
#
# 覆蓋的方式是**改 `os.environ` 而且在 import app 之前**，不是 import 之後
# 去改 `config.DATABASE_URL`。理由是 `test_cors.py` 會 `importlib.reload(config)`
# 來驗「CORS_ORIGINS=* 要在啟動時炸掉」—— reload 會整個重跑 `app/config.py`，
# 把改在模組屬性上的值打回 `.env` 的開發位址。實測過：單檔跑沒事，整套跑到
# `test_cors.py` 之後的每個測試都在砍開發庫，而 237 個測試照樣全綠。
# ─────────────────────────────────────────────────────────────────────────

_ENV = {**dotenv_values(Path(__file__).resolve().parent.parent / ".env"), **os.environ}
TEST_DATABASE_URL = _ENV.get("TEST_DATABASE_URL")

if TEST_DATABASE_URL and TEST_DATABASE_URL == _ENV.get("DATABASE_URL"):
    raise RuntimeError(
        "TEST_DATABASE_URL 不得與 DATABASE_URL 相同 —— 測試會 drop schema，"
        f"指向同一個庫等於每次跑測試都清空開發資料。目前兩者都是：{TEST_DATABASE_URL}"
    )

if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from app import config, db as db_module  # noqa: E402
from app.main import app  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def db_unavailable() -> str | None:
    """只探測一次資料庫在不在，回傳失敗原因（在的話回 None）。

    沒有這層的話，每個需要資料庫的測試都要各自等一次連線逾時 —— 四十幾個
    測試就是好幾分鐘，慢到大家會開始習慣性跳過整個測試套件。
    """
    import asyncio

    if not TEST_DATABASE_URL:
        return (
            "沒有設 TEST_DATABASE_URL。測試會 drop schema，所以必須指定一個"
            "專用的庫；刻意不退回 DATABASE_URL，那會清空開發資料。"
        )

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
        pytest.skip(f"沒有可用的測試資料庫：{db_unavailable}")

    # 要砍東西之前先確認砍的是測試庫。
    #
    # 這道檢查不是多餘的：`config.DATABASE_URL` 在測試過程中會被
    # `importlib.reload(config)` 改掉（見上面）。沒有這一行的話，那種改動的
    # 症狀是「開發資料悄悄消失而測試全綠」—— 要好幾天才會有人發現，而且
    # 那時已經分不出是誰砍的。
    if config.DATABASE_URL != TEST_DATABASE_URL:
        raise RuntimeError(
            "測試要 drop schema，但 app.config 目前指向的不是測試庫："
            f"config.DATABASE_URL={config.DATABASE_URL}，"
            f"TEST_DATABASE_URL={TEST_DATABASE_URL}。"
            "有東西在測試過程中重新載入了 app.config（例如 importlib.reload）。"
        )

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
