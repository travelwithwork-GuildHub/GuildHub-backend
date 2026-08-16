"""[R01] WS 訊息協定 v1 序列化往返。規格來源：開工前準備 附錄 A。

這些測試釘住兩個刻意的設計（附錄 A.4）：
  · x / y 是整數，不接受浮點
  · pos 差量用陣列 ["<id>", x, y, f]，不用物件
"""

import json

import pytest

from app.realtime import protocol as p

# ---------------------------------------------------------------- client → server


def test_move_round_trip():
    raw = {"t": "move", "x": 120, "y": 340, "f": 2}
    msg = p.parse_client(json.dumps(raw))
    assert isinstance(msg, p.Move)
    assert (msg.x, msg.y, msg.f) == (120, 340, 2)


@pytest.mark.parametrize("bad", [120.5, "120", None])
def test_move_rejects_non_integer_coordinates(bad):
    """附錄 A.2：x／y 為整數像素，不送浮點（省封包）。"""
    with pytest.raises(p.ProtocolError):
        p.parse_client(json.dumps({"t": "move", "x": bad, "y": 340, "f": 2}))


@pytest.mark.parametrize("f", [-1, 4, 99])
def test_move_rejects_facing_out_of_range(f):
    """附錄 A.2：f = facing，0=下 1=左 2=右 3=上。"""
    with pytest.raises(p.ProtocolError):
        p.parse_client(json.dumps({"t": "move", "x": 1, "y": 2, "f": f}))


def test_status_round_trip():
    msg = p.parse_client(json.dumps({"t": "status", "text": "趕工中"}))
    assert isinstance(msg, p.StatusIn)
    assert msg.text == "趕工中"


def test_chat_round_trip():
    msg = p.parse_client(json.dumps({"t": "chat", "body": "有人在嗎"}))
    assert isinstance(msg, p.ChatIn)
    assert msg.body == "有人在嗎"


@pytest.mark.parametrize(
    "raw",
    [
        '{"t":"nope"}',  # 未定義的型別
        '{"x":1,"y":2,"f":0}',  # 缺 t
        "not json at all",
        "[]",
        '{"t":"move","x":1}',  # 缺欄位
    ],
)
def test_unparseable_client_messages_raise(raw):
    with pytest.raises(p.ProtocolError):
        p.parse_client(raw)


def test_client_cannot_forge_server_message_types():
    """hello / snapshot / pos / presence 只能由伺服器發出。"""
    for t in ("hello", "snapshot", "pos", "presence", "err"):
        with pytest.raises(p.ProtocolError):
            p.parse_client(json.dumps({"t": t}))


# ---------------------------------------------------------------- server → client


def test_hello():
    assert json.loads(p.hello("u-1")) == {"t": "hello", "you": "u-1", "hz": 10}


def test_snapshot():
    players = [
        {"id": "u-1", "name": "阿凱", "av": 0, "x": 0, "y": 0, "f": 0, "st": ""},
        {"id": "u-2", "name": "小明", "av": 3, "x": 64, "y": 32, "f": 2, "st": "趕工中"},
    ]
    out = json.loads(p.snapshot(players))
    assert out["t"] == "snapshot"
    assert out["players"] == players


def test_positions_use_arrays_not_objects():
    """附錄 A.4：40 人 × 10 Hz 下，欄位名會被重複送 400 次／秒。"""
    out = json.loads(p.positions([("u-1", 120, 340, 2), ("u-2", 88, 210, 0)]))
    assert out["t"] == "pos"
    assert out["p"] == [["u-1", 120, 340, 2], ["u-2", 88, 210, 0]]
    for entry in out["p"]:
        assert isinstance(entry, list), "位置差量必須是陣列，不得是物件"


def test_presence_join_and_leave():
    joined = [{"id": "u-9", "name": "新來的", "av": 1, "x": 0, "y": 0, "f": 0, "st": ""}]
    out = json.loads(p.presence(join=joined, leave=["u-3"]))
    assert out == {"t": "presence", "join": joined, "leave": ["u-3"]}


def test_status_out():
    assert json.loads(p.status_out("u-1", "開會中")) == {
        "t": "status",
        "id": "u-1",
        "text": "開會中",
    }


def test_chat_out():
    assert json.loads(p.chat_out("u-1", "阿凱", "先開會")) == {
        "t": "chat",
        "id": "u-1",
        "name": "阿凱",
        "body": "先開會",
    }


def test_err():
    assert json.loads(p.err("bad_token", "room token 無效")) == {
        "t": "err",
        "code": "bad_token",
        "msg": "room token 無效",
    }


def test_every_server_message_carries_a_type_tag():
    produced = [
        p.hello("u"),
        p.snapshot([]),
        p.positions([("u", 1, 2, 3)]),
        p.presence(join=[], leave=["u"]),
        p.status_out("u", "x"),
        p.chat_out("u", "n", "b"),
        p.err("c", "m"),
    ]
    for raw in produced:
        assert "t" in json.loads(raw)
