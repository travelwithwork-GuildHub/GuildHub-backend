"""[R06] 兩個 scene 的成員互不可見。規格書 §3.1。

廣播範圍即 scene 邊界 —— 大廳看不到房間、房間看不到大廳，隔音由連線歸屬
自然達成，不需額外的可見度判斷。這裡用真的 SceneRegistry，不用 mock。
"""

import pytest

from app.realtime.scenes import SceneRegistry


class Conn:
    """成員的替身。registry 不在意成員是什麼，只在意它屬於哪個 scene。"""

    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f"Conn({self.name})"


def test_scene_isolation_members_do_not_leak():
    reg = SceneRegistry()
    a, b = Conn("大廳的人"), Conn("房間的人")

    reg.add_member("lobby", a)
    reg.add_member("room:p1", b)

    assert list(reg.members("lobby")) == [a]
    assert list(reg.members("room:p1")) == [b]


def test_scene_isolation_two_rooms_do_not_see_each_other():
    reg = SceneRegistry()
    a, b = Conn("p1 的人"), Conn("p2 的人")
    reg.add_member("room:p1", a)
    reg.add_member("room:p2", b)

    assert a not in reg.members("room:p2")
    assert b not in reg.members("room:p1")


def test_scene_isolation_online_count_is_per_scene():
    reg = SceneRegistry()
    for i in range(3):
        reg.add_member("lobby", Conn(f"l{i}"))
    for i in range(2):
        reg.add_member("room:p1", Conn(f"r{i}"))

    assert reg.online_count("lobby") == 3
    assert reg.online_count("room:p1") == 2
    assert reg.online_count("room:p9") == 0  # 沒人的房間不必先存在


def test_scene_isolation_survives_removal():
    reg = SceneRegistry()
    a, b = Conn("a"), Conn("b")
    reg.add_member("lobby", a)
    reg.add_member("room:p1", b)

    reg.remove_member("lobby", a)

    assert reg.online_count("lobby") == 0
    assert reg.online_count("room:p1") == 1  # 另一個 scene 不受影響


def test_get_or_create_is_idempotent():
    reg = SceneRegistry()
    assert reg.get_or_create("lobby") is reg.get_or_create("lobby")
    assert reg.get_or_create("room:p1") is not reg.get_or_create("room:p2")


@pytest.mark.parametrize(
    "scene_id", ["lobby", "room:11111111-2222-3333-4444-555555555555", "room:p1"]
)
def test_accepts_the_two_legal_scene_id_shapes(scene_id):
    assert SceneRegistry().get_or_create(scene_id).id == scene_id


@pytest.mark.parametrize("scene_id", ["", "hall", "room:", "room", "lobby:1", "ROOM:p1"])
def test_rejects_everything_else(scene_id):
    """[R07]：scene_id 只有這兩種格式。"""
    with pytest.raises(ValueError):
        SceneRegistry().get_or_create(scene_id)


def test_remove_member_is_forgiving():
    """斷線可能重複觸發（[R24] 與心跳逾時），移除不存在的成員不該炸。"""
    reg = SceneRegistry()
    a = Conn("a")
    reg.add_member("lobby", a)
    reg.remove_member("lobby", a)
    reg.remove_member("lobby", a)
    reg.remove_member("room:p1", a)
    assert reg.online_count("lobby") == 0
