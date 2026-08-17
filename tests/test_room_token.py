"""[P43] RoomTokenService：偽造 token 被拒；過期 token 被拒。規格書 §6.2。

房間是共享密碼制（不做成員制）。密碼驗過之後換到的就是這個 token，WS 握手
時憑它進場。因此這是全案唯一擋在「房間隔音」前面的東西。
"""

import time

import pytest

from app import room_token


def test_room_token_round_trip():
    tok = room_token.issue("proj-1", "user-9")
    claims = room_token.verify(tok)
    assert claims.project_id == "proj-1"
    assert claims.user_id == "user-9"


def test_room_token_rejects_a_forged_signature():
    tok = room_token.issue("proj-1", "user-9")
    payload, _, _sig = tok.partition(".")
    forged = f"{payload}.{'a' * 43}"
    with pytest.raises(room_token.InvalidRoomToken):
        room_token.verify(forged)


def test_room_token_rejects_a_tampered_payload():
    """改掉 project_id 想混進別的房間 —— 簽章對不上。"""
    tok = room_token.issue("proj-1", "user-9")
    other = room_token.issue("proj-2", "user-9")
    spliced = other.split(".")[0] + "." + tok.split(".")[1]
    with pytest.raises(room_token.InvalidRoomToken):
        room_token.verify(spliced)


def test_room_token_rejects_an_expired_token():
    tok = room_token.issue("proj-1", "user-9", ttl_seconds=-1)
    with pytest.raises(room_token.InvalidRoomToken):
        room_token.verify(tok)


def test_room_token_accepts_one_that_has_not_expired_yet():
    tok = room_token.issue("proj-1", "user-9", ttl_seconds=60)
    assert room_token.verify(tok).project_id == "proj-1"
    assert room_token.verify(tok).expires_at > time.time()


@pytest.mark.parametrize(
    "junk", ["", ".", "abc", "abc.def", "a.b.c", "沒有點的東西", None]
)
def test_room_token_rejects_garbage(junk):
    with pytest.raises(room_token.InvalidRoomToken):
        room_token.verify(junk)


def test_room_token_signature_depends_on_the_secret(monkeypatch):
    """換了 secret，舊 token 立刻失效 —— 這是重啟後房間會要求重新輸入密碼的依據。"""
    from app import config

    tok = room_token.issue("proj-1", "user-9")
    monkeypatch.setattr(config, "ROOM_TOKEN_SECRET", "另一把鑰匙")
    with pytest.raises(room_token.InvalidRoomToken):
        room_token.verify(tok)


def test_room_token_two_users_get_different_tokens():
    a = room_token.issue("proj-1", "user-a")
    b = room_token.issue("proj-1", "user-b")
    assert a != b
    assert room_token.verify(a).user_id == "user-a"
    assert room_token.verify(b).user_id == "user-b"
