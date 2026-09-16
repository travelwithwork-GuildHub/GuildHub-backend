"""[BE-G12] Project Resources：專案房間裡的外部連結清單。

設計與裁決紀錄在工作區的 `FE-J14-BE-G12-工作清單.md`（權限 × 錯誤碼那張表）
與 `BE-G12-FE-J14-contract-draft.md` §11。這個檔案是那張表的可執行版本。

三件跟直覺不同、但都是刻意的事：

1. **room token 只給讀取權。** 進得了房間不代表改得了持久資料 —— 寫入一律
   只有發起人。所以「持票的人 POST 回 403」是這裡最重要的一條。
2. **長度與 URL scheme 違反回 500，不是 422。** 長度規則的唯一來源是
   `sql/001_schema.sql`（[P15]），應用層刻意不重寫，所以超長會是資料庫錯誤。
   這跟 profiles、messages 的既有行為一致。
3. **未知欄位靜默忽略。** Pydantic 預設 `extra='ignore'`，全 repo 沒有任何
   `extra` 設定；送 `id` 進來不會被拒絕，但也絕不會改到那一列的 id。

結案（closed）的房間仍然拿得到 room token（`close` 不清 `password_hash`），
所以「結案後只有發起人讀得到」必須由這些端點自己查 status，不能靠 token。
那是下面 `test_closed_room_is_invisible_to_a_ticket_holder` 在盯的事。
"""

import asyncio
import datetime as dt
import time
import uuid

import asyncpg
import httpx
import pytest

from app.main import app

pytestmark = pytest.mark.db

PASSWORD = "guild1234"

GITHUB = {"label": "Repository", "type": "github", "url": "https://github.com/org/repo"}


# --------------------------------------------------------------------- 工具


async def recruiting(login, who: str = "發起人") -> tuple[str, object]:
    """開一個還在招募中的專案，回傳 (project_id, 發起人的 client)。"""
    owner = await login(who)
    created = await owner.post(
        "/api/projects", json={"title": "資源測試", "body": "內文", "seat_count": 8}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"], owner


async def active(login, who: str = "發起人") -> tuple[str, object]:
    """開一個已成軍的專案。"""
    project_id, owner = await recruiting(login, who)
    formed = await owner.post(
        f"/api/projects/{project_id}/form-team", json={"password": PASSWORD}
    )
    assert formed.status_code == 200, formed.text
    return project_id, owner


async def another_active(owner) -> str:
    """同一個發起人再開一個已成軍的專案。"""
    created = await owner.post(
        "/api/projects", json={"title": "另一個專案", "body": "內文", "seat_count": 8}
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    formed = await owner.post(
        f"/api/projects/{project_id}/form-team", json={"password": PASSWORD}
    )
    assert formed.status_code == 200, formed.text
    return project_id


async def enter(client, project_id: str) -> None:
    """通過房間密碼，把 room token 放進這個 client 的 session。"""
    response = await client.post(
        f"/api/projects/{project_id}/enter", json={"password": PASSWORD}
    )
    assert response.status_code == 200, response.text


async def add(client, project_id: str, **overrides) -> dict:
    payload = {**GITHUB, **overrides}
    response = await client.post(f"/api/projects/{project_id}/resources", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def path(project_id: str, resource_id: str) -> str:
    return f"/api/projects/{project_id}/resources/{resource_id}"


def assert_detail(response, expected: str) -> None:
    """4xx 的 body 形狀是 `{"detail": 中文字串}`（422 除外，那是 Pydantic 的陣列）。"""
    assert response.json() == {"detail": expected}, response.text


async def snapshot(db) -> list[dict]:
    """整張表的內容。被拒絕的請求之後要跟之前一模一樣 —— 只比筆數的話，
    「沒有新增但改掉了 label」會被當成沒事。"""
    rows = await db.fetch(
        "select id, project_id, label, type, url, created_at "
        "from project_resources order by id"
    )
    return [dict(row) for row in rows]


async def expect_database_error(client, method: str, url: str, **kwargs) -> None:
    """資料庫 check 擋下的請求：對外是 500 text/plain，不是 422。

    測試用的 client 預設把 app 內的例外直接拋出來（`raise_app_exceptions`），
    那樣只能寫 `pytest.raises(Exception)`，任何例外都算過。這裡另開一個不拋的
    client、沿用同一組 cookie，斷言真正送到前端的回應。
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        cookies=client.cookies,
    ) as raw:
        response = await raw.request(method, url, **kwargs)

    assert response.status_code == 500, response.text
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == "Internal Server Error"


async def wait_until_blocked(db, count: int, tasks) -> None:
    """等到恰好有 `count` 個請求卡在資料庫鎖上。

    並行測試不能靠「剛好同時」：測試用的連線池一開始只有一條連線，五個請求
    會被現場開連線的時間排成先後，拿掉鎖也照樣綠（審查實測 0/5 紅）。所以由
    測試自己持鎖、確認每個請求都已經在等，才放手。

    請求自己先結束了（沒有等鎖），就立刻失敗，不必等到逾時。
    """
    deadline = time.monotonic() + 10
    while True:
        waiting = await db.fetchval("select count(*) from pg_locks where not granted")
        if waiting >= count:
            return
        finished = [t for t in tasks if t.done()]
        assert not finished, (
            f"請求沒有等 project 列的鎖就結束了：{[t.result().status_code for t in finished]}"
        )
        assert time.monotonic() < deadline, f"只有 {waiting} 個請求在等鎖，預期 {count}"
        await asyncio.sleep(0.05)


class RowHolder:
    """另一條連線，在交易裡對 project 列做某件事，先不提交。"""

    def __init__(self, db):
        self.db = db
        self.open = False

    async def __aenter__(self):
        self.conn = await self.db.acquire()
        self.tx = self.conn.transaction()
        await self.tx.start()
        self.open = True
        return self

    async def commit(self) -> None:
        self.open = False
        await self.tx.commit()

    async def rollback(self) -> None:
        self.open = False
        await self.tx.rollback()

    async def __aexit__(self, *exc):
        if self.open:
            await self.tx.rollback()
        await self.db.release(self.conn)


# ------------------------------------------------------------- 基本讀寫行為


async def test_new_room_has_an_empty_list(db, login):
    """沒有資源時回空陣列，不是 404 —— 「還沒有東西」不是錯誤。"""
    project_id, owner = await active(login)

    response = await owner.get(f"/api/projects/{project_id}/resources")

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_created_resource_comes_back_with_every_field(db, login):
    project_id, owner = await active(login)

    created = await add(owner, project_id)

    assert created["label"] == "Repository"
    assert created["type"] == "github"
    assert created["url"] == "https://github.com/org/repo"
    assert created["project_id"] == project_id
    assert uuid.UUID(created["id"])  # 不是空字串，也不是整數序號
    assert created["created_at"]
    # 這一列真的進了資料庫，不是只在回應裡看起來對。
    stored = await db.fetchval(
        "select count(*) from project_resources where id = $1",
        uuid.UUID(created["id"]),
    )
    assert stored == 1


@pytest.mark.parametrize("kind", ["github", "figma", "notion", "drive", "meeting"])
async def test_every_supported_type_is_accepted(db, login, kind):
    """五種都要能存。meeting 是使用者自己貼的常設會議室，跟洽談用的一次性
    連結（FE-N03）沒有關係 —— 那個不在這一包裡。"""
    project_id, owner = await active(login)

    created = await add(owner, project_id, type=kind)

    assert created["type"] == kind


async def test_unsupported_type_is_rejected_by_the_application(db, login):
    """`other` V1 不做。type 由 Pydantic enum 擋，所以是 422 而不是資料庫錯誤
    —— 這跟長度不同：type 的合法值是有限集合，鏡像 SQL 的 enum 寫在 model 裡
    有先例（ProjectStatus）。"""
    project_id, owner = await active(login)

    response = await owner.post(
        f"/api/projects/{project_id}/resources", json={**GITHUB, "type": "other"}
    )

    assert response.status_code == 422, response.text


async def test_list_is_ordered_by_creation_then_id(db, login):
    """固定排序，沒有 sort_order 欄位也沒有 reorder 端點。"""
    project_id, owner = await active(login)

    first = await add(owner, project_id, label="一")
    second = await add(owner, project_id, label="二")
    third = await add(owner, project_id, label="三")

    listed = (await owner.get(f"/api/projects/{project_id}/resources")).json()

    assert [r["label"] for r in listed] == ["一", "二", "三"]
    assert [r["id"] for r in listed] == [first["id"], second["id"], third["id"]]


async def test_list_order_comes_from_the_query_not_from_insert_order(db, login):
    """上一條拿掉 ORDER BY 也會綠：剛寫進去的列，讀出來本來就是寫入順序。

    這裡直接對資料庫寫，讓寫入先後跟 (created_at, id) 剛好相反 —— 包含
    created_at 相同時靠 id 決定的那一段。
    """
    project_id, owner = await active(login)
    pid = uuid.UUID(project_id)
    base = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    tie = base + dt.timedelta(minutes=30)
    for label, resource_id, created_at in [
        ("最晚", uuid.uuid4(), base + dt.timedelta(hours=1)),
        ("同時・後", uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff"), tie),
        ("同時・先", uuid.UUID("00000000-0000-4000-8000-000000000000"), tie),
        ("最早", uuid.uuid4(), base),
    ]:
        await db.execute(
            "insert into project_resources (id, project_id, label, type, url, created_at) "
            "values ($1, $2, $3, 'github', 'https://example.com', $4)",
            resource_id,
            pid,
            label,
            created_at,
        )

    listed = (await owner.get(f"/api/projects/{project_id}/resources")).json()

    assert [r["label"] for r in listed] == ["最早", "同時・先", "同時・後", "最晚"]


async def test_url_is_stored_exactly_as_given(db, login):
    """不做 URL 正規化：大小寫、query 都原樣存、原樣回。"""
    project_id, owner = await active(login)
    url = "HTTPS://GitHub.com/Org/Repo?Tab=Issues"

    created = await add(owner, project_id, url=url)

    assert created["url"] == url
    stored = await db.fetchval(
        "select url from project_resources where id = $1", uuid.UUID(created["id"])
    )
    assert stored == url


async def test_two_resources_may_share_one_url(db, login):
    """不設 unique、不做 URL 正規化：同一個 repo 用兩個標籤指過去是合理的。"""
    project_id, owner = await active(login)

    await add(owner, project_id, label="程式碼")
    await add(owner, project_id, label="Issues")

    listed = (await owner.get(f"/api/projects/{project_id}/resources")).json()
    assert len(listed) == 2
    assert listed[0]["url"] == listed[1]["url"]


# ------------------------------------------------------------------- PATCH


async def test_patch_changes_only_the_given_field(db, login):
    """真正的 partial update：沒給的欄位要原封不動。"""
    project_id, owner = await active(login)
    created = await add(owner, project_id)

    response = await owner.patch(
        path(project_id, created["id"]), json={"url": "https://github.com/org/new"}
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["url"] == "https://github.com/org/new"
    assert updated["label"] == created["label"], "沒給的欄位被動到了"
    assert updated["type"] == created["type"]
    assert updated["created_at"] == created["created_at"], "建立時間不是可以改的東西"


async def test_patch_with_an_empty_body_returns_the_row_unchanged(db, login):
    """送 `{}` 是合法的，回 200 原樣 —— 沿用 PATCH /api/profiles/me 的行為。
    改成 422 的話，前端「沒有改動就不送」的判斷會變成必要的，而那是應用層
    規則兩份不一致的開始。"""
    project_id, owner = await active(login)
    created = await add(owner, project_id)

    response = await owner.patch(path(project_id, created["id"]), json={})

    assert response.status_code == 200, response.text
    assert response.json() == created


async def test_patch_ignores_fields_that_are_not_editable(db, login):
    """未知欄位靜默忽略（Pydantic 預設），但**值絕不能被改到**。

    這一條同時盯兩件事：不是 422（不然前端的本地後端要多一套規則），
    也不是「照收」（不然 id 會被呼叫端指定）。
    """
    project_id, owner = await active(login)
    created = await add(owner, project_id)
    intruder = str(uuid.uuid4())

    response = await owner.patch(
        path(project_id, created["id"]),
        json={
            "id": intruder,
            "project_id": intruder,
            "created_at": "2000-01-01T00:00:00Z",
            "nonsense": "x",
            "label": "改標籤",
        },
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["label"] == "改標籤", "合法欄位還是要改得動"
    assert updated["id"] == created["id"]
    assert updated["project_id"] == created["project_id"]
    assert updated["created_at"] == created["created_at"]


@pytest.mark.parametrize("field", ["label", "type", "url"])
async def test_patch_rejects_an_explicit_null(db, login, field):
    """明確送 null 是「把這欄清空」的意思，而這三欄在資料庫都是 NOT NULL，
    所以是應用層擋（422），不讓它走到資料庫。"""
    project_id, owner = await active(login)
    created = await add(owner, project_id)

    response = await owner.patch(path(project_id, created["id"]), json={field: None})

    assert response.status_code == 422, response.text


async def test_patch_on_a_resource_from_another_project_is_404(db, login):
    """resource_id 存在，但不屬於 path 上的那個 project —— 不能因為「東西存在」
    就讓它被改到。

    兩個專案都是同一個發起人的，否則先擋下來的會是 403（非發起人），
    這條就驗不到「path 與 resource 要對得起來」。
    """
    mine, owner = await active(login, "我")
    other = await another_active(owner)
    created = await add(owner, mine)

    before = await snapshot(db)

    response = await owner.patch(path(other, created["id"]), json={"label": "x"})

    assert response.status_code == 404, response.text
    assert_detail(response, "資源不存在")
    assert await snapshot(db) == before


async def test_empty_patch_on_a_resource_from_another_project_is_404(db, login):
    """`{}` 走的是另一句 SQL（只讀不寫），那一句也要限定在 path 的 project 內。
    漏掉的話，發起人用自己的專案路徑就能讀到任何人任何一筆資源。"""
    mine, owner = await active(login, "我")
    other = await another_active(owner)
    created = await add(owner, mine)

    response = await owner.patch(path(other, created["id"]), json={})

    assert response.status_code == 404, response.text
    assert_detail(response, "資源不存在")


async def test_patch_on_a_missing_resource_is_404(db, login):
    project_id, owner = await active(login)

    response = await owner.patch(path(project_id, str(uuid.uuid4())), json={"label": "x"})

    assert response.status_code == 404, response.text
    assert_detail(response, "資源不存在")


async def test_patch_after_creation_keeps_the_list_order(db, login):
    """改過的那一筆不會跑到最後 —— 排序看 created_at，不看最後修改。"""
    project_id, owner = await active(login)
    first = await add(owner, project_id, label="一")
    await add(owner, project_id, label="二")

    await owner.patch(path(project_id, first["id"]), json={"label": "改過的一"})

    listed = (await owner.get(f"/api/projects/{project_id}/resources")).json()
    assert [r["label"] for r in listed] == ["改過的一", "二"]


async def test_patch_may_duplicate_another_url(db, login):
    project_id, owner = await active(login)
    await add(owner, project_id, label="一", url="https://example.com/a")
    second = await add(owner, project_id, label="二", url="https://example.com/b")

    response = await owner.patch(
        path(project_id, second["id"]), json={"url": "https://example.com/a"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["url"] == "https://example.com/a"


async def test_patch_that_breaks_a_database_check_is_500_and_changes_nothing(db, login):
    project_id, owner = await active(login)
    created = await add(owner, project_id)
    before = await snapshot(db)

    await expect_database_error(
        owner, "PATCH", path(project_id, created["id"]), json={"label": "字" * 101}
    )

    assert await snapshot(db) == before


# ------------------------------------------------------------------ DELETE


async def test_delete_removes_it_from_the_list(db, login):
    """硬刪除，沒有 deleted_at。刪掉之後列表看不到，資料庫也沒有。"""
    project_id, owner = await active(login)
    created = await add(owner, project_id)

    response = await owner.delete(path(project_id, created["id"]))

    assert response.status_code == 204, response.text
    assert response.content == b""
    listed = (await owner.get(f"/api/projects/{project_id}/resources")).json()
    assert listed == []
    remaining = await db.fetchval(
        "select count(*) from project_resources where id = $1",
        uuid.UUID(created["id"]),
    )
    assert remaining == 0


async def test_deleting_twice_is_404_the_second_time(db, login):
    """刪除不是冪等的成功 —— 第二次要說「這個東西不在」，否則前端分不出
    「我剛剛刪掉了」與「有人先刪了」。"""
    project_id, owner = await active(login)
    created = await add(owner, project_id)

    await owner.delete(path(project_id, created["id"]))
    again = await owner.delete(path(project_id, created["id"]))

    assert again.status_code == 404, again.text
    assert_detail(again, "資源不存在")


async def test_delete_on_a_resource_from_another_project_is_404(db, login):
    """用自己專案 P 的路徑，不能刪掉 Q 的資源。兩個專案同一個發起人，理由同 PATCH 那條。"""
    mine, owner = await active(login, "我")
    other = await another_active(owner)
    created = await add(owner, mine)
    before = await snapshot(db)

    response = await owner.delete(path(other, created["id"]))

    assert response.status_code == 404, response.text
    assert_detail(response, "資源不存在")
    assert await snapshot(db) == before


# ------------------------------------------------------- 權限：誰讀得到、誰寫得動


async def test_ticket_holder_can_read(db, login):
    """進了房的人讀得到資源 —— 這就是這張看板存在的理由。"""
    project_id, owner = await active(login)
    await add(owner, project_id)
    member = await login("組員")
    await enter(member, project_id)

    response = await member.get(f"/api/projects/{project_id}/resources")

    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


async def test_ticket_holder_cannot_write(db, login):
    """**這條是這個檔案最重要的一條。** room token 是「我通過了房間密碼」，
    不是「我可以改這個專案的持久資料」。沒有成員制，只有發起人與其他人。"""
    project_id, owner = await active(login)
    created = await add(owner, project_id)
    member = await login("組員")
    await enter(member, project_id)
    before = await snapshot(db)

    posted = await member.post(f"/api/projects/{project_id}/resources", json=GITHUB)
    patched = await member.patch(path(project_id, created["id"]), json={"label": "x"})
    deleted = await member.delete(path(project_id, created["id"]))

    assert [posted.status_code, patched.status_code, deleted.status_code] == [
        403,
        403,
        403,
    ]
    for response in (posted, patched, deleted):
        assert_detail(response, "只有發起人可以做這件事")
    # 而且真的沒有寫進去，也沒有改到。
    assert await snapshot(db) == before


async def test_logged_in_stranger_without_a_ticket_cannot_write(db, login):
    project_id, owner = await active(login)
    created = await add(owner, project_id)
    stranger = await login("路人")
    before = await snapshot(db)

    posted = await stranger.post(f"/api/projects/{project_id}/resources", json=GITHUB)
    patched = await stranger.patch(path(project_id, created["id"]), json={"label": "x"})
    deleted = await stranger.delete(path(project_id, created["id"]))

    assert [posted.status_code, patched.status_code, deleted.status_code] == [403, 403, 403]
    for response in (posted, patched, deleted):
        assert_detail(response, "只有發起人可以做這件事")
    assert await snapshot(db) == before


async def test_logged_in_stranger_without_a_ticket_cannot_read(db, login):
    """登入了但沒進房 = 沒有讀取權。看板上有連結，連結本身就是內容。"""
    project_id, owner = await active(login)
    await add(owner, project_id)
    stranger = await login("路人")

    response = await stranger.get(f"/api/projects/{project_id}/resources")

    assert response.status_code == 403, response.text
    assert_detail(response, "尚未通過房間密碼驗證")


async def test_owner_reads_without_entering_the_room(db, login):
    """發起人不必先輸入自己的房間密碼。"""
    project_id, owner = await active(login)
    await add(owner, project_id)

    response = await owner.get(f"/api/projects/{project_id}/resources")

    assert response.status_code == 200, response.text


async def test_missing_project_is_404_for_everyone(db, login):
    """斷言 detail，不只斷言 404：路由不存在時 FastAPI 也回 404（`Not Found`），
    只看狀態碼的話，這條在端點還沒寫的時候就是綠的。"""
    stranger = await login("路人")
    missing = uuid.uuid4()
    some_resource = uuid.uuid4()

    responses = [
        await stranger.get(f"/api/projects/{missing}/resources"),
        await stranger.post(f"/api/projects/{missing}/resources", json=GITHUB),
        await stranger.patch(path(str(missing), str(some_resource)), json={"label": "x"}),
        await stranger.delete(path(str(missing), str(some_resource))),
    ]

    assert [r.status_code for r in responses] == [404, 404, 404, 404]
    for response in responses:
        assert_detail(response, "專案不存在")


# ------------------------------------------------------------ 生命週期：狀態


async def test_recruiting_project_reads_empty_for_its_owner(db, login):
    """還沒成軍就沒有房間，也就沒有資源可看；但那不是錯誤，回空陣列。"""
    project_id, owner = await recruiting(login)

    response = await owner.get(f"/api/projects/{project_id}/resources")

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_recruiting_project_rejects_writes(db, login):
    """狀態不允許寫入 → 409（衝突），不是 422。輸入沒有問題，是時機不對。"""
    project_id, owner = await recruiting(login)

    response = await owner.post(f"/api/projects/{project_id}/resources", json=GITHUB)

    assert response.status_code == 409, response.text
    assert_detail(response, "專案還沒成軍，還沒有房間可以放資源")
    assert await db.fetchval("select count(*) from project_resources") == 0


async def test_recruiting_project_is_403_for_a_stranger(db, login):
    """招募中的專案沒有密碼、拿不到票，所以路人等同「沒進房」。"""
    project_id, _owner = await recruiting(login)
    stranger = await login("路人")

    response = await stranger.get(f"/api/projects/{project_id}/resources")

    assert response.status_code == 403, response.text


async def test_closed_project_is_still_readable_by_its_owner(db, login):
    """結案之後資源不會消失 —— close 不刪 project 列，cascade 不會觸發。
    發起人事後還要找得到那些連結。"""
    project_id, owner = await active(login)
    await add(owner, project_id)
    await owner.post(f"/api/projects/{project_id}/close", json={})

    response = await owner.get(f"/api/projects/{project_id}/resources")

    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


async def test_closed_project_rejects_every_write(db, login):
    """結案之後誰都不能寫，包含發起人本人。"""
    project_id, owner = await active(login)
    created = await add(owner, project_id)
    await owner.post(f"/api/projects/{project_id}/close", json={})
    before = await snapshot(db)

    posted = await owner.post(f"/api/projects/{project_id}/resources", json=GITHUB)
    patched = await owner.patch(path(project_id, created["id"]), json={"label": "x"})
    deleted = await owner.delete(path(project_id, created["id"]))

    assert [posted.status_code, patched.status_code, deleted.status_code] == [
        409,
        409,
        409,
    ]
    for response in (posted, patched, deleted):
        assert_detail(response, "專案已結案，資源不能再修改")
    assert await snapshot(db) == before


async def test_closed_project_is_403_not_409_for_a_non_owner_write(db, login):
    """非發起人對結案專案寫入：先說「你不是發起人」，不是「專案已結案」。
    409 會把「這個專案曾經可以讓你改」的錯覺交給前端。"""
    project_id, owner = await active(login)
    created = await add(owner, project_id)
    member = await login("組員")
    await enter(member, project_id)
    await owner.post(f"/api/projects/{project_id}/close", json={})
    before = await snapshot(db)

    posted = await member.post(f"/api/projects/{project_id}/resources", json=GITHUB)
    patched = await member.patch(path(project_id, created["id"]), json={"label": "x"})
    deleted = await member.delete(path(project_id, created["id"]))

    assert [posted.status_code, patched.status_code, deleted.status_code] == [403, 403, 403]
    for response in (posted, patched, deleted):
        assert_detail(response, "只有發起人可以做這件事")
    assert await snapshot(db) == before


async def test_closed_project_is_403_for_a_stranger_without_a_ticket(db, login):
    project_id, owner = await active(login)
    await add(owner, project_id)
    await owner.post(f"/api/projects/{project_id}/close", json={})
    stranger = await login("路人")

    response = await stranger.get(f"/api/projects/{project_id}/resources")

    assert response.status_code == 403, response.text
    assert_detail(response, "尚未通過房間密碼驗證")


async def test_closed_room_is_invisible_to_a_ticket_holder(db, login):
    """**結案的房間仍然簽得出 room token** —— `close` 只改 status、清座位，
    不清 `password_hash`，而 `/enter` 只看密碼對不對。所以「結案後只有發起人
    讀得到」必須由這個端點自己查 status；靠 token 是擋不住的。

    這一條在前端 `FE-N08` 的規格裡被獨立確認過（結案仍拿得到票）。
    """
    project_id, owner = await active(login)
    await add(owner, project_id)
    member = await login("組員")
    await enter(member, project_id)
    await owner.post(f"/api/projects/{project_id}/close", json={})

    # 票還在手上，而且還是有效的 —— 甚至現在才去換票也換得到。
    latecomer = await login("結案後才來的人")
    ticket = await latecomer.post(
        f"/api/projects/{project_id}/enter", json={"password": PASSWORD}
    )
    assert ticket.status_code == 200, "前提變了：結案後已經換不到票，這條要重寫"

    assert (await member.get(f"/api/projects/{project_id}/resources")).status_code == 403
    assert (
        await latecomer.get(f"/api/projects/{project_id}/resources")
    ).status_code == 403


# ------------------------------------------------------------- 邊界與驗證


@pytest.mark.parametrize("length", [1, 100])
async def test_label_at_the_boundary_is_accepted(db, login, length):
    project_id, owner = await active(login)

    created = await add(owner, project_id, label="字" * length)

    assert created["label"] == "字" * length


async def test_label_one_over_the_limit_is_a_database_error(db, login):
    """[P15] 的延續：長度只寫在 `sql/001_schema.sql`，應用層不重寫，所以超長
    是資料庫錯誤（對外是 500），不是 422。前端自己擋長度。"""
    project_id, owner = await active(login)

    await expect_database_error(
        owner, "POST", f"/api/projects/{project_id}/resources", json={**GITHUB, "label": "字" * 101}
    )

    assert await db.fetchval("select count(*) from project_resources") == 0


async def test_emoji_label_counts_code_points_not_bytes(db, login):
    """100 個 emoji 是 100 個字，不是 400 個 byte —— `char_length` 算的是
    code point。這條在的理由是：如果哪天有人把 check 改成算 byte，
    一個合法的標籤會突然被拒。"""
    project_id, owner = await active(login)

    created = await add(owner, project_id, label="😀" * 100)

    assert created["label"] == "😀" * 100


async def test_emoji_label_one_over_the_limit_is_a_database_error(db, login):
    """跟上一條成對：101 個 emoji 要被擋。只有接受那一邊的話，check 拿掉也會綠。"""
    project_id, owner = await active(login)

    await expect_database_error(
        owner, "POST", f"/api/projects/{project_id}/resources", json={**GITHUB, "label": "😀" * 101}
    )

    assert await db.fetchval("select count(*) from project_resources") == 0


async def test_blank_label_is_rejected(db, login):
    """只有空白的標籤在畫面上等於沒有名字。由資料庫的 `btrim(label) <> ''`
    擋下，所以跟長度一樣是資料庫錯誤。"""
    project_id, owner = await active(login)

    await expect_database_error(
        owner, "POST", f"/api/projects/{project_id}/resources", json={**GITHUB, "label": "   "}
    )

    assert await db.fetchval("select count(*) from project_resources") == 0


async def test_url_at_the_length_boundary_is_accepted(db, login):
    project_id, owner = await active(login)
    url = "https://example.com/" + "a" * (2048 - len("https://example.com/"))

    created = await add(owner, project_id, url=url)

    assert len(created["url"]) == 2048


async def test_url_one_over_the_limit_is_a_database_error(db, login):
    project_id, owner = await active(login)
    url = "https://example.com/" + "a" * (2049 - len("https://example.com/"))

    await expect_database_error(
        owner, "POST", f"/api/projects/{project_id}/resources", json={**GITHUB, "url": url}
    )

    assert await db.fetchval("select count(*) from project_resources") == 0


@pytest.mark.parametrize("scheme", ["javascript:alert(1)", "data:text/html,x", "file:///c:/", "ftp://example.com"])
async def test_non_http_schemes_never_reach_the_database(db, login, scheme):
    """存進去的東西會被別人的瀏覽器打開。放行的只有 http 與 https，
    由資料庫的 regex 擋 —— 前端另外還有一層 `safeHref`，兩層都要在。"""
    project_id, owner = await active(login)

    await expect_database_error(
        owner, "POST", f"/api/projects/{project_id}/resources", json={**GITHUB, "url": scheme}
    )

    assert await db.fetchval("select count(*) from project_resources") == 0


@pytest.mark.parametrize("url", ["http://example.com/x", "https://example.com/x"])
async def test_both_http_and_https_are_accepted(db, login, url):
    project_id, owner = await active(login)

    created = await add(owner, project_id, url=url)

    assert created["url"] == url


@pytest.mark.parametrize("missing", ["label", "type", "url"])
async def test_create_requires_every_field(db, login, missing):
    project_id, owner = await active(login)
    payload = {k: v for k, v in GITHUB.items() if k != missing}

    response = await owner.post(f"/api/projects/{project_id}/resources", json=payload)

    assert response.status_code == 422, response.text


async def test_malformed_path_uuid_is_422(db, login):
    """路徑參數不是 UUID：FastAPI 在路由層就擋下，不會走到資料庫。"""
    _project_id, owner = await active(login)

    response = await owner.get("/api/projects/not-a-uuid/resources")

    assert response.status_code == 422, response.text


async def test_malformed_resource_uuid_is_422(db, login):
    project_id, owner = await active(login)

    patched = await owner.patch(f"/api/projects/{project_id}/resources/not-a-uuid", json={})
    deleted = await owner.delete(f"/api/projects/{project_id}/resources/not-a-uuid")

    assert [patched.status_code, deleted.status_code] == [422, 422]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"content": b"{not json", "headers": {"content-type": "application/json"}}, id="壞 JSON"),
        pytest.param({"content": b'{"label": "L", "type": "github", "url": "https://a.b"}'}, id="缺 Content-Type"),
        pytest.param({"json": {**GITHUB, "label": 123}}, id="型別錯"),
        pytest.param({"json": {**GITHUB, "label": None}}, id="明確 null"),
    ],
)
async def test_malformed_create_bodies_are_422(db, login, body):
    project_id, owner = await active(login)

    response = await owner.post(f"/api/projects/{project_id}/resources", **body)

    assert response.status_code == 422, response.text
    assert await db.fetchval("select count(*) from project_resources") == 0


async def test_create_ignores_unknown_fields(db, login):
    """未知欄位靜默忽略（R3）。`id` 帶進來不會變成那一列的 id。"""
    project_id, owner = await active(login)
    intruder = str(uuid.uuid4())

    created = await add(owner, project_id, id=intruder, created_at="2000-01-01T00:00:00Z", nonsense=1)

    assert created["id"] != intruder
    assert not created["created_at"].startswith("2000")
    assert await db.fetchval(
        "select count(*) from project_resources where id = $1", uuid.UUID(intruder)
    ) == 0


async def test_type_check_is_enforced_by_the_database_itself(db, login):
    """schema 不變式：應用層的 enum 先擋掉了，所以 API 永遠碰不到這個 check。
    直接寫 SQL 驗，比照下面外鍵與 cascade 的做法。"""
    project_id, _owner = await active(login)

    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "insert into project_resources (project_id, label, type, url) "
            "values ($1, 'x', 'other', 'https://example.com')",
            uuid.UUID(project_id),
        )


# --------------------------------------------- 上限 50 筆與並行（R6 / R10）


async def test_the_fifty_first_resource_is_rejected(db, login):
    """每個專案最多 50 筆。這是跨兩張表的條件（project 要 active、這個 project
    已經有幾筆），寫不成資料庫約束，所以擋在應用層 —— 擋法見下一條。"""
    project_id, owner = await active(login)
    for i in range(50):
        await add(owner, project_id, label=f"第 {i} 筆")

    response = await owner.post(f"/api/projects/{project_id}/resources", json=GITHUB)

    assert response.status_code == 409, response.text
    assert await db.fetchval("select count(*) from project_resources") == 50


async def test_concurrent_creates_cannot_exceed_the_limit(db, login):
    """**循序測不出來的那一條。** 49 筆的狀態下五個請求同時進來，如果計數與
    寫入不是在同一把鎖之後，五個都會讀到 49、五個都會寫進去。

    不靠「剛好同時」：另一條連線先鎖住 project 列，確認五個請求都卡在鎖上
    才放手。放手之後它們一個一個拿到鎖 —— 這時候如果計數跟鎖寫在同一句，
    那一句的快照在等鎖之前就拍好了，五個都會看到 49（見 project_resources.py 檔頭）。

    恰好一個成功，其餘 409，資料庫裡不多不少 50 筆。
    """
    project_id, owner = await active(login)
    await db.execute(
        "insert into project_resources (project_id, label, type, url) "
        "select $1, '第 ' || n || ' 筆', 'github', 'https://example.com' "
        "from generate_series(1, 49) as n",
        uuid.UUID(project_id),
    )

    async with RowHolder(db) as holder:
        await holder.conn.execute(
            "select id from projects where id = $1 for update", uuid.UUID(project_id)
        )
        tasks = [
            asyncio.create_task(
                owner.post(f"/api/projects/{project_id}/resources", json=GITHUB)
            )
            for _ in range(5)
        ]
        await wait_until_blocked(db, 5, tasks)
        await holder.rollback()

    codes = sorted(r.status_code for r in await asyncio.gather(*tasks))
    assert codes.count(201) == 1, f"應該恰好一個成功，實際 {codes}"
    assert codes.count(409) == 4, f"其餘都該是 409，實際 {codes}"
    assert await db.fetchval("select count(*) from project_resources") == 50


WRITES = {
    "POST": lambda client, project_id, resource_id: client.post(
        f"/api/projects/{project_id}/resources", json=GITHUB
    ),
    "PATCH": lambda client, project_id, resource_id: client.patch(
        path(project_id, resource_id), json={"label": "改"}
    ),
    "DELETE": lambda client, project_id, resource_id: client.delete(
        path(project_id, resource_id)
    ),
}
SUCCESS = {"POST": 201, "PATCH": 200, "DELETE": 204}


@pytest.mark.parametrize("method", list(WRITES))
async def test_a_write_waits_for_the_project_row_lock(db, login, method):
    """寫入端點自己有鎖住 project 列 —— 不是被外鍵順便擋住。

    持鎖的那一邊用 `FOR NO KEY UPDATE`，也就是 close 那句 `UPDATE projects`
    拿的鎖。它擋得住端點的 `FOR UPDATE`，但**擋不住**外鍵檢查（`FOR KEY SHARE`）：
    改用 `FOR UPDATE` 持鎖的話，端點就算沒有鎖，POST 也會因為外鍵而等，這條
    就驗不到端點自己的鎖。PATCH、DELETE 不碰外鍵，沒鎖就完全不會等。
    """
    project_id, owner = await active(login)
    created = await add(owner, project_id)

    async with RowHolder(db) as holder:
        await holder.conn.execute(
            "select id from projects where id = $1 for no key update",
            uuid.UUID(project_id),
        )
        task = asyncio.create_task(WRITES[method](owner, project_id, created["id"]))
        await wait_until_blocked(db, 1, [task])
        await holder.rollback()

    # 鎖放開之後同一個請求要成功 —— 證明上面是等鎖，不是壞掉。
    response = await task
    assert response.status_code == SUCCESS[method], response.text


@pytest.mark.parametrize("method", list(WRITES))
async def test_a_write_that_queued_behind_close_is_rejected(db, login, method):
    """順序一：close 先拿到列、寫入在後面等。close 提交之後，寫入要看到的是
    closed —— 409，而且表上什麼都沒變。

    持鎖的那一邊跑的就是 close 的那句 UPDATE，先不提交。
    """
    project_id, owner = await active(login)
    created = await add(owner, project_id)
    before = await snapshot(db)

    async with RowHolder(db) as holder:
        await holder.conn.execute(
            "update projects set status = 'closed', updated_at = now() where id = $1",
            uuid.UUID(project_id),
        )
        task = asyncio.create_task(WRITES[method](owner, project_id, created["id"]))
        await wait_until_blocked(db, 1, [task])
        await holder.commit()

    response = await task
    assert response.status_code == 409, response.text
    assert_detail(response, "專案已結案，資源不能再修改")
    assert await snapshot(db) == before


async def test_close_that_queued_behind_a_write_lands_after_it(db, login):
    """順序二：寫入先拿到列（鎖住並寫了一筆，還沒提交），close 在後面等。
    寫入提交之後 close 才成功；那一筆留下來，而之後再也寫不進去。

    持鎖的那一邊照端點的兩句做：先 `FOR UPDATE`，再 insert。
    """
    project_id, owner = await active(login)

    async with RowHolder(db) as holder:
        await holder.conn.execute(
            "select id from projects where id = $1 for update", uuid.UUID(project_id)
        )
        await holder.conn.execute(
            "insert into project_resources (project_id, label, type, url) "
            "values ($1, '先寫的', 'github', 'https://example.com')",
            uuid.UUID(project_id),
        )
        task = asyncio.create_task(
            owner.post(f"/api/projects/{project_id}/close", json={})
        )
        await wait_until_blocked(db, 1, [task])
        await holder.commit()

    closed = await task
    assert closed.status_code == 200, closed.text
    assert await db.fetchval("select count(*) from project_resources") == 1

    late = await owner.post(f"/api/projects/{project_id}/resources", json=GITHUB)
    assert late.status_code == 409, late.text
    assert await db.fetchval("select count(*) from project_resources") == 1


async def test_concurrent_patch_and_delete_of_one_resource(db, login):
    """同一筆同時被改與刪：兩者排隊，結果只有兩種，而且都不是 500。

    PATCH 先 → 200，DELETE 再 → 204；DELETE 先 → 204，PATCH 再 → 404。
    """
    project_id, owner = await active(login)
    created = await add(owner, project_id)

    async with RowHolder(db) as holder:
        await holder.conn.execute(
            "select id from projects where id = $1 for update", uuid.UUID(project_id)
        )
        patched = asyncio.create_task(WRITES["PATCH"](owner, project_id, created["id"]))
        deleted = asyncio.create_task(WRITES["DELETE"](owner, project_id, created["id"]))
        await wait_until_blocked(db, 2, [patched, deleted])
        await holder.rollback()

    codes = ((await patched).status_code, (await deleted).status_code)
    assert codes in {(200, 204), (404, 204)}, codes
    assert await db.fetchval("select count(*) from project_resources") == 0


# ------------------------------------------------------- schema 層的不變式


async def test_deleting_a_project_cascades_to_its_resources(db, login):
    """schema 不變式，不是 API 行為：沒有刪除專案的端點，所以直接對資料庫下。
    比照 test_projects.py 的座位 cascade 測試。
    """
    project_id, owner = await active(login)
    await add(owner, project_id)

    await db.execute("delete from projects where id = $1", uuid.UUID(project_id))

    left = await db.fetchval(
        "select count(*) from project_resources where project_id = $1",
        uuid.UUID(project_id),
    )
    assert left == 0


async def test_a_resource_cannot_point_at_a_missing_project(db):
    """外鍵是真的在 —— 沒有這條，上面那條 cascade 也可能只是碰巧沒資料。"""
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(
            "insert into project_resources (project_id, label, type, url) "
            "values ($1, 'x', 'github', 'https://example.com')",
            uuid.uuid4(),
        )
