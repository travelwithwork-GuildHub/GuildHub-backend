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


# ------------------------------------------------- [BE-G28] 專案欄位的不變式
#
# 前端 2026-09-19 的清單第 1.1 條：POST /api/projects 對空標題、0 個座位、
# 9 個座位、300 字標題全部回 201。前端表單擋得住，但直接打 API 就繞過了。
#
# 修法刻意**不是**在 models.py 加 Field —— 長度與範圍的唯一真實來源是這個
# schema（CLAUDE.md、models.py 開頭的長註解、test_error_codes.py 的「長度是
# 資料庫的事」）。所以下面這幾條跟上面四條是同一件事：繞過應用層直接寫，
# 資料庫仍然要擋。
#
# 每一條都斷言 constraint 名稱，因為那個名稱就是回給前端的錯誤 `code`
# （見 tests/test_error_payload.py）—— 改名等於改對外契約。


async def test_invariant_project_title_cannot_be_blank(db):
    """機制：projects_title_length。空白不只是「短」，btrim 之後是 0 字。"""
    owner = await make_profile(db)

    for blank in ("", "   ", "\n\t"):
        with pytest.raises(asyncpg.CheckViolationError) as exc:
            await db.execute(
                "insert into projects (owner_id, title, body) values ($1, $2, $3)",
                owner,
                blank,
                "內文",
            )
        assert exc.value.constraint_name == "projects_title_length"


async def test_invariant_project_title_has_an_upper_bound(db):
    """機制：projects_title_length。60 字進得去，61 字進不去。"""
    owner = await make_profile(db)

    ok = await db.fetchval(
        "insert into projects (owner_id, title, body) values ($1, $2, $3) returning id",
        owner,
        "標" * 60,
        "內文",
    )
    assert ok is not None, "60 字是上限本身，必須過"

    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await db.execute(
            "insert into projects (owner_id, title, body) values ($1, $2, $3)",
            owner,
            "標" * 61,
            "內文",
        )
    assert exc.value.constraint_name == "projects_title_length"


async def test_invariant_project_body_matches_message_body(db):
    """機制：projects_body_length。上限跟 messages.body 同樣是 2000。"""
    owner = await make_profile(db)

    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await db.execute(
            "insert into projects (owner_id, title, body) values ($1, $2, $3)",
            owner,
            "標題",
            "字" * 2001,
        )
    assert exc.value.constraint_name == "projects_body_length"

    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await db.execute(
            "insert into projects (owner_id, title, body) values ($1, $2, $3)",
            owner,
            "標題",
            "   ",
        )
    assert exc.value.constraint_name == "projects_body_length"


@pytest.mark.parametrize("seat_count", [0, 9, -1])
async def test_invariant_seat_count_must_fit_the_seat_table(db, seat_count):
    """機制：projects_seat_count_range（1–8）。

    上限 8 不是隨便選的，它是 seats.seat_in_range（0 ≤ seat_index < 8）的
    另一面：seat_count = 9 的房間，第 9 格永遠坐不到，claim_seat 會回 400。
    0 個座位則是「成軍之後沒有人坐得下」。兩個數字必須同源。
    """
    owner = await make_profile(db)

    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await db.execute(
            "insert into projects (owner_id, title, body, seat_count) "
            "values ($1, $2, $3, $4)",
            owner,
            "標題",
            "內文",
            seat_count,
        )
    assert exc.value.constraint_name == "projects_seat_count_range"


async def test_invariant_needed_skills_has_a_count_limit(db):
    """機制：projects_needed_skills_count（最多 10 項）。"""
    owner = await make_profile(db)

    ok = await db.fetchval(
        "insert into projects (owner_id, title, body, needed_skills) "
        "values ($1, $2, $3, $4) returning id",
        owner,
        "標題",
        "內文",
        [f"技能{i}" for i in range(10)],
    )
    assert ok is not None, "10 項是上限本身，必須過"

    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await db.execute(
            "insert into projects (owner_id, title, body, needed_skills) "
            "values ($1, $2, $3, $4)",
            owner,
            "標題",
            "內文",
            [f"技能{i}" for i in range(11)],
        )
    assert exc.value.constraint_name == "projects_needed_skills_count"


async def test_invariant_needed_skills_has_a_total_length_limit(db):
    """機制：projects_needed_skills_total_length（總長 ≤ 450）。

    **這一條是妥協，寫在這裡免得日後有人以為是漏掉的。** 前端要的是「每項
    1–40 字」，但 PostgreSQL 的 CHECK 不能含子查詢，unnest 進不去，逐項檢查
    只能靠在 schema 裡引入一個 immutable 函式 —— 那是這個 repo 沒有的機制。

    所以擋的是總長：塞 1000 個 tag（數量擋）或塞一整篇文章（總長擋）都過不了，
    但單獨一個 300 字的 skill 會過。每項 1–40 由前端表單負責，這件事寫在
    給前端的回覆文件裡，不是默默放過。
    """
    owner = await make_profile(db)

    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await db.execute(
            "insert into projects (owner_id, title, body, needed_skills) "
            "values ($1, $2, $3, $4)",
            owner,
            "標題",
            "內文",
            ["字" * 200, "字" * 300],
        )
    assert exc.value.constraint_name == "projects_needed_skills_total_length"
