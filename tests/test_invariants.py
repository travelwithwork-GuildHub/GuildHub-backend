"""[P03] 規格書 §4.1 的四個關鍵不變式。

    「這些條件寫在資料庫而非應用層，因為應用層會有 bug，約束不會。」

因此每個測試都必須斷言「是資料庫擋下的」——  抓 asyncpg 的特定例外型別，
而不是抓通用 Exception。若哪天有人把約束改寫成應用層的 if 判斷，這裡就會
紅，這正是這四個測試存在的理由（任務表 [P09]：若有 case 靠應用層才過，
視為未完成）。
"""

import uuid

import asyncpg
import pytest

pytestmark = pytest.mark.db


async def make_profile(pool, name: str = "測試員") -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute(
        "insert into profiles (id, display_name) values ($1, $2)", user_id, name
    )
    return user_id


async def make_project(pool, owner_id: uuid.UUID) -> uuid.UUID:
    return await pool.fetchval(
        "insert into projects (owner_id, title, body) values ($1, $2, $3) returning id",
        owner_id,
        "測試專案",
        "內文",
    )


# ------------------------------------------------- 1. 不存在「成軍了但房間沒開」


async def test_invariant_cannot_activate_a_project_without_a_room(db):
    """機制：room_ready check。

    成軍 = status 轉 active，同時必須有 room_template 與 password_hash。
    這個綁定消除了四種需要額外處理的狀態（§6.1）。
    """
    owner = await make_profile(db)
    project = await make_project(db, owner)

    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await db.execute(
            "update projects set status = 'active' where id = $1", project
        )
    assert "room_ready" in str(exc.value)


async def test_invariant_activating_with_a_room_is_allowed(db):
    """反面：備妥房間就過得去。否則上面那個測試可能只是因為 update 根本不通。"""
    owner = await make_profile(db)
    project = await make_project(db, owner)

    await db.execute(
        "update projects set status = 'active', room_template = 1, "
        "password_hash = 'x' where id = $1",
        project,
    )
    assert await db.fetchval(
        "select status from projects where id = $1", project
    ) == "active"


# --------------------------------------------------------- 2. 兩人不會搶到同一座位


async def test_invariant_two_people_cannot_take_the_same_seat(db):
    """機制：seats primary key (project_id, seat_index)。"""
    owner = await make_profile(db, "發起人")
    first = await make_profile(db, "先搶到的")
    second = await make_profile(db, "慢一步的")
    project = await make_project(db, owner)

    await db.execute(
        "insert into seats (project_id, seat_index, user_id) values ($1, 0, $2)",
        project,
        first,
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "insert into seats (project_id, seat_index, user_id) values ($1, 0, $2)",
            project,
            second,
        )


# ------------------------------------------------------------- 3. 一人不會佔兩個座位


async def test_invariant_one_person_cannot_hold_two_seats(db):
    """機制：seats unique (project_id, user_id)。"""
    owner = await make_profile(db, "發起人")
    greedy = await make_profile(db, "想佔兩格的")
    project = await make_project(db, owner)

    await db.execute(
        "insert into seats (project_id, seat_index, user_id) values ($1, 0, $2)",
        project,
        greedy,
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "insert into seats (project_id, seat_index, user_id) values ($1, 1, $2)",
            project,
            greedy,
        )


# ------------------------------------------------------------------ 4. 不能寄信給自己


async def test_invariant_cannot_send_a_message_to_yourself(db):
    """機制：no_self_send check。"""
    me = await make_profile(db)

    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await db.execute(
            "insert into messages (sender_id, recipient_id, body) values ($1, $1, $2)",
            me,
            "自言自語",
        )
    assert "no_self_send" in str(exc.value)


# ------------------------------------------- 附帶：專案結束時座位自動釋放（cascade）


async def test_invariant_closing_a_project_releases_its_seats(db):
    """機制：on delete cascade（§4.1 第四列）。

    [P34] 會再從服務層驗一次；這裡先確認約束本身在。
    """
    owner = await make_profile(db, "發起人")
    member = await make_profile(db, "成員")
    project = await make_project(db, owner)
    await db.execute(
        "insert into seats (project_id, seat_index, user_id) values ($1, 0, $2)",
        project,
        member,
    )

    await db.execute("delete from projects where id = $1", project)

    assert await db.fetchval("select count(*) from seats where project_id = $1", project) == 0


async def test_invariant_seat_index_must_be_in_range(db):
    """機制：seat_in_range check（0 ≤ seat_index < 8）。"""
    owner = await make_profile(db, "發起人")
    member = await make_profile(db, "成員")
    project = await make_project(db, owner)

    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "insert into seats (project_id, seat_index, user_id) values ($1, 8, $2)",
            project,
            member,
        )
