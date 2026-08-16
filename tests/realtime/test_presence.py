"""[R11] PresenceStore：set 後 snapshot 含之；狀態文字超 12 字被拒。

規格書 §3.3：狀態文字存在 presence payload，不進資料庫；登入時為空白，
離線即清空 —— 結構上不可能出現過期狀態。
"""

import pytest

from app.realtime.presence import PresenceStore

SNAPSHOT_KEYS = {"id", "name", "av", "x", "y", "f", "st"}


def store_with(*people):
    s = PresenceStore()
    for uid, name, scene in people:
        s.join(uid, name=name, scene=scene)
    return s


# ------------------------------------------------------------------ [R12] 位置


def test_presence_set_position_then_snapshot_contains_it():
    s = store_with(("u1", "阿凱", "lobby"))
    s.set_position("u1", 120, 340, 2)

    (player,) = s.snapshot("lobby")
    assert (player["id"], player["x"], player["y"], player["f"]) == ("u1", 120, 340, 2)


def test_presence_snapshot_shape_matches_protocol():
    """附錄 A.3：snapshot 的元素是 {id,name,av,x,y,f,st}。"""
    s = store_with(("u1", "阿凱", "lobby"))
    (player,) = s.snapshot("lobby")
    assert set(player) == SNAPSHOT_KEYS


def test_presence_snapshot_is_scoped_to_one_scene():
    """規格書 §3.1：大廳看不到房間。"""
    s = store_with(("u1", "大廳", "lobby"), ("u2", "房間", "room:p1"))

    assert [p["id"] for p in s.snapshot("lobby")] == ["u1"]
    assert [p["id"] for p in s.snapshot("room:p1")] == ["u2"]
    assert s.snapshot("room:p9") == []


def test_presence_new_player_starts_at_origin_with_blank_status():
    s = store_with(("u1", "阿凱", "lobby"))
    (player,) = s.snapshot("lobby")
    assert (player["x"], player["y"], player["f"]) == (0, 0, 0)
    assert player["st"] == ""  # §3.3：登入時為空白


# -------------------------------------------------------------- [R13] 狀態文字


def test_presence_status_text_over_12_chars_is_rejected():
    """§3.3 上限 12 字。伺服器自己驗，不依賴前端截斷（任務表 [R13]）。"""
    s = store_with(("u1", "阿凱", "lobby"))
    with pytest.raises(ValueError):
        s.set_status_text("u1", "這段狀態文字整整有十三個字")  # 13


def test_presence_status_text_at_the_limit_is_accepted():
    s = store_with(("u1", "阿凱", "lobby"))
    s.set_status_text("u1", "十二個字剛剛好可以通過")  # 11
    s.set_status_text("u1", "正好十二個字的狀態文字啦")  # 12
    (player,) = s.snapshot("lobby")
    assert player["st"] == "正好十二個字的狀態文字啦"


def test_presence_status_text_counts_characters_not_bytes():
    """中文一個字是 3 bytes；用 bytes 算會讓 4 個字就被擋下。"""
    s = store_with(("u1", "阿凱", "lobby"))
    s.set_status_text("u1", "趕工中")
    assert s.snapshot("lobby")[0]["st"] == "趕工中"


def test_presence_status_text_is_broadcast_not_persisted():
    """§3.3：不進資料庫。看 import 而不是看原始碼文字 —— 註解裡提到 app.db
    不算違規，真的 import 才算。"""
    import ast

    import app.realtime.presence as mod

    with open(mod.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    forbidden = ("app.db", "asyncpg", "psycopg", "sqlalchemy", "redis")
    leaked = [m for m in imported if m.startswith(forbidden)]
    assert not leaked, f"presence.py 不得依賴持久層，但 import 了 {leaked}"


# -------------------------------------------------- 差量追蹤（供 [R20] 使用）


def test_presence_diff_reports_only_movers():
    s = store_with(("u1", "動的", "lobby"), ("u2", "不動的", "lobby"))
    s.diff_since_last_tick("lobby")  # 清掉 join 造成的初始差量

    s.set_position("u1", 10, 20, 1)

    assert s.diff_since_last_tick("lobby") == [("u1", 10, 20, 1)]


def test_presence_diff_is_empty_when_nobody_moved():
    s = store_with(("u1", "阿凱", "lobby"))
    s.diff_since_last_tick("lobby")
    assert s.diff_since_last_tick("lobby") == []


def test_presence_diff_clears_after_being_read():
    s = store_with(("u1", "阿凱", "lobby"))
    s.set_position("u1", 5, 5, 0)
    assert s.diff_since_last_tick("lobby") != []
    assert s.diff_since_last_tick("lobby") == []


def test_presence_diff_does_not_cross_scenes():
    s = store_with(("u1", "大廳", "lobby"), ("u2", "房間", "room:p1"))
    s.diff_since_last_tick("lobby")
    s.diff_since_last_tick("room:p1")

    s.set_position("u2", 9, 9, 3)

    assert s.diff_since_last_tick("lobby") == []
    assert s.diff_since_last_tick("room:p1") == [("u2", 9, 9, 3)]


def test_presence_setting_the_same_position_is_not_movement():
    """靜止時封包數必須為 0（§3.2）—— 重送同一座標不算移動。"""
    s = store_with(("u1", "阿凱", "lobby"))
    s.set_position("u1", 7, 7, 0)
    s.diff_since_last_tick("lobby")

    s.set_position("u1", 7, 7, 0)

    assert s.diff_since_last_tick("lobby") == []


# ------------------------------------------------------------------ [R27] 清空


def test_presence_clear_removes_the_player_from_snapshot():
    s = store_with(("u1", "阿凱", "lobby"), ("u2", "小明", "lobby"))
    s.clear("u1")
    assert [p["id"] for p in s.snapshot("lobby")] == ["u2"]


def test_presence_clear_discards_status_text():
    """§3.3：離線即清空 —— 狀態文字不得留存至下次登入。"""
    s = store_with(("u1", "阿凱", "lobby"))
    s.set_status_text("u1", "開會中")
    s.clear("u1")

    s.join("u1", name="阿凱", scene="lobby")
    assert s.snapshot("lobby")[0]["st"] == ""


def test_presence_clear_is_forgiving():
    PresenceStore().clear("誰都不是")


def test_presence_operations_on_unknown_user_do_not_create_ghosts():
    s = PresenceStore()
    s.set_position("鬼", 1, 2, 3)
    s.set_status_text("鬼", "在")
    assert s.snapshot("lobby") == []
