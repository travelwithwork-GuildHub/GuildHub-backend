"""FastAPI app。規格書 §2.2：同一 process 同時掛載 REST 與 WebSocket。"""

import contextlib
import logging

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app import config, db

log = logging.getLogger("guildhub")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # 即時層不需要資料庫。連不上時仍要能啟動，否則軌 R 會被軌 P 擋住。
    try:
        await db.connect()
    except Exception as exc:  # noqa: BLE001
        log.warning("資料庫未連上，僅即時層可用：%s", exc)
    yield
    await db.disconnect()


app = FastAPI(title="GuildHub", version="0.1", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET)


@app.get("/health")
async def health():
    return {"status": "ok"}
