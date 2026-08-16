"""[R04] 驗收：可獨立 import 組出合法訊息，不需 server。"""

import json

import pytest

from app.realtime import protocol
from tools.fake_client import FakeClient


def test_builds_valid_messages_without_a_server():
    c = FakeClient("測試員")
    assert c.ws is None  # 沒連任何東西

    for raw in (c.move_msg(120, 340, 2), c.status_msg("趕工中"), c.chat_msg("嗨")):
        protocol.parse_client(raw)  # 組出來的一定是合法的 client 訊息


def test_move_msg_keeps_coordinates_integral():
    out = json.loads(FakeClient("x").move_msg(120, 340, 2))
    assert out == {"t": "move", "x": 120, "y": 340, "f": 2}
    assert isinstance(out["x"], int) and isinstance(out["y"], int)


def test_cannot_build_an_illegal_message():
    c = FakeClient("x")
    with pytest.raises(Exception):
        c.move_msg(1.5, 2, 0)
    with pytest.raises(Exception):
        c.move_msg(1, 2, 9)


def test_room_url_carries_token_lobby_does_not():
    assert FakeClient("x", scene="lobby").url.endswith("/ws?scene=lobby")
    assert "&token=abc" in FakeClient("x", scene="room:p1", token="abc").url
