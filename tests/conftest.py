"""測試共用 fixture。

軌 R 的測試需要一個真的 server —— fake_client 走真實 WebSocket，用 mock 或
ASGI 測試客戶端就驗不到節流與封包量（任務表 [R17] [R18]：必須實際計數封包）。
"""

import socket
import threading
import time

import pytest
import uvicorn

from app.main import app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
