"""[P10] 假資料的靜態檢查。

002_seed.sql 要等 [D01] 才跑得起來，但那時如果有一列違反約束，整個 seed 會
在中途炸掉，而發現的時機是 P2 正想看看板的時候。

所以這裡先用純文字解析把最容易踩到的幾條約束檢查過一遍。這不能取代真的
跑一次，但可以讓真的跑那次不要浪費在低級錯誤上。
"""

import re
import uuid
from pathlib import Path

SEED = Path(__file__).resolve().parent.parent / "sql" / "002_seed.sql"


def statements() -> list[str]:
    text = re.sub(r"--[^\n]*", "", SEED.read_text(encoding="utf-8"))
    return [s.strip() for s in text.split(";") if s.strip()]


def rows_of(table: str) -> list[str]:
    """撈出某個 insert 的 values 區塊，逐列回傳。"""
    for stmt in statements():
        if not re.match(rf"insert\s+into\s+{table}\b", stmt, re.I):
            continue
        values = stmt[re.search(r"\bvalues\b", stmt, re.I).end():]
        values = re.sub(r"on\s+conflict.*$", "", values, flags=re.I | re.S)
        # 以「) , (」為界切列，避免被字串裡的逗號騙到
        return [m.group(1) for m in re.finditer(r"\(((?:[^()']|'[^']*')*)\)", values)]
    return []


def uuids_in(text: str) -> list[str]:
    return re.findall(
        r"'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'", text
    )


def test_seed_file_exists_and_is_not_empty():
    assert SEED.exists()
    assert len(statements()) >= 4


def test_seed_uuids_are_well_formed():
    for raw in uuids_in(SEED.read_text(encoding="utf-8")):
        uuid.UUID(raw)  # 格式不對會直接拋錯


def test_seed_gives_both_boards_at_least_eight_rows():
    """任務表 [P10]：兩個看板各 ≥ 8 筆。"""
    assert len(rows_of("profiles")) >= 8, "人才看板不足 8 筆"
    assert len(rows_of("projects")) >= 8, "任務看板不足 8 筆"


def test_seed_never_sends_a_message_to_self():
    """no_self_send check —— 踩到的話整個 seed 會在這一列中止。"""
    for row in rows_of("messages"):
        ids = uuids_in(row)
        assert len(ids) >= 2, f"站內信缺少寄件或收件人：{row[:60]}"
        assert ids[0] != ids[1], f"站內信寄給自己：{row[:60]}"


def test_seed_seat_indexes_are_in_range_and_unique():
    """seats 的 primary key、unique 與 seat_in_range 三條一起檢查。"""
    seen_slots: set[tuple[str, int]] = set()
    seen_people: set[tuple[str, str]] = set()

    for row in rows_of("seats"):
        ids = uuids_in(row)
        project_id, user_id = ids[0], ids[1]
        seat_index = int(re.search(r",\s*(\d+)\s*,", row).group(1))

        assert 0 <= seat_index < 8, f"seat_index 超出範圍：{seat_index}"

        slot = (project_id, seat_index)
        assert slot not in seen_slots, f"同一格坐了兩個人：{slot}"
        seen_slots.add(slot)

        person = (project_id, user_id)
        assert person not in seen_people, f"同一人佔了兩格：{person}"
        seen_people.add(person)


def test_seed_active_projects_have_a_room():
    """room_ready check：status = active 就必須有 room_template 與 password_hash。"""
    for stmt in statements():
        if not re.match(r"insert\s+into\s+projects\b", stmt, re.I):
            continue
        if "'active'" not in stmt:
            continue
        assert "room_template" in stmt and "password_hash" in stmt, (
            "有 active 專案但欄位清單缺 room_template／password_hash，"
            "room_ready check 會擋下整個 insert"
        )
        for row in re.finditer(r"'active',\s*(\d+),\s*'(scrypt\$[^']+)'", stmt):
            assert row.group(2).count("$") == 2


def test_seed_uses_obviously_fictional_people():
    """規格書 §9：不捏造真人，不使用同學姓名或照片。

    只能做形式檢查 —— 確認沒有出現照片路徑或 email 這類會指向真人的東西。
    """
    text = SEED.read_text(encoding="utf-8")
    assert not re.search(r"https?://", text), "假資料不該含外部連結或照片網址"
    assert "@" not in text, "假資料不該含 email"
