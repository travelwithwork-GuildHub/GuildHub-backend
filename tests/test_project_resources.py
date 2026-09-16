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
import uuid

import asyncpg
import pytest

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

    response = await owner.patch(path(other, created["id"]), json={"label": "x"})

    assert response.status_code == 404, response.text


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

    posted = await member.post(f"/api/projects/{project_id}/resources", json=GITHUB)
    patched = await member.patch(path(project_id, created["id"]), json={"label": "x"})
    deleted = await member.delete(path(project_id, created["id"]))

    assert [posted.status_code, patched.status_code, deleted.status_code] == [
        403,
        403,
        403,
    ]
    # 而且真的沒有寫進去。
    assert await db.fetchval("select count(*) from project_resources") == 1


async def test_logged_in_stranger_without_a_ticket_cannot_read(db, login):
    """登入了但沒進房 = 沒有讀取權。看板上有連結，連結本身就是內容。"""
    project_id, owner = await active(login)
    await add(owner, project_id)
    stranger = await login("路人")

    response = await stranger.get(f"/api/projects/{project_id}/resources")

    assert response.status_code == 403, response.text


async def test_owner_reads_without_entering_the_room(db, login):
    """發起人不必先輸入自己的房間密碼。"""
    project_id, owner = await active(login)
    await add(owner, project_id)

    response = await owner.get(f"/api/projects/{project_id}/resources")

    assert response.status_code == 200, response.text


async def test_missing_project_is_404_for_everyone(db, login):
    stranger = await login("路人")
    missing = uuid.uuid4()

    listed = await stranger.get(f"/api/projects/{missing}/resources")
    posted = await stranger.post(f"/api/projects/{missing}/resources", json=GITHUB)

    assert listed.status_code == 404, listed.text
    assert posted.status_code == 404, posted.text


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

    posted = await owner.post(f"/api/projects/{project_id}/resources", json=GITHUB)
    patched = await owner.patch(path(project_id, created["id"]), json={"label": "x"})
    deleted = await owner.delete(path(project_id, created["id"]))

    assert [posted.status_code, patched.status_code, deleted.status_code] == [
        409,
        409,
        409,
    ]
    assert await db.fetchval("select count(*) from project_resources") == 1


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

    with pytest.raises(Exception):
        await owner.post(
            f"/api/projects/{project_id}/resources", json={**GITHUB, "label": "字" * 101}
        )

    assert await db.fetchval("select count(*) from project_resources") == 0


async def test_emoji_label_counts_code_points_not_bytes(db, login):
    """100 個 emoji 是 100 個字，不是 400 個 byte —— `char_length` 算的是
    code point。這條在的理由是：如果哪天有人把 check 改成算 byte，
    一個合法的標籤會突然被拒。"""
    project_id, owner = await active(login)

    created = await add(owner, project_id, label="😀" * 100)

    assert created["label"] == "😀" * 100


async def test_blank_label_is_rejected(db, login):
    """只有空白的標籤在畫面上等於沒有名字。由資料庫的 `btrim(label) <> ''`
    擋下，所以跟長度一樣是資料庫錯誤。"""
    project_id, owner = await active(login)

    with pytest.raises(Exception):
        await owner.post(
            f"/api/projects/{project_id}/resources", json={**GITHUB, "label": "   "}
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

    with pytest.raises(Exception):
        await owner.post(f"/api/projects/{project_id}/resources", json={**GITHUB, "url": url})

    assert await db.fetchval("select count(*) from project_resources") == 0


@pytest.mark.parametrize("scheme", ["javascript:alert(1)", "data:text/html,x", "file:///c:/", "ftp://example.com"])
async def test_non_http_schemes_never_reach_the_database(db, login, scheme):
    """存進去的東西會被別人的瀏覽器打開。放行的只有 http 與 https，
    由資料庫的 regex 擋 —— 前端另外還有一層 `safeHref`，兩層都要在。"""
    project_id, owner = await active(login)

    with pytest.raises(Exception):
        await owner.post(f"/api/projects/{project_id}/resources", json={**GITHUB, "url": scheme})

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

    恰好一個成功，其餘 409，資料庫裡不多不少 50 筆。
    """
    project_id, owner = await active(login)
    for i in range(49):
        await add(owner, project_id, label=f"第 {i} 筆")

    responses = await asyncio.gather(
        *(
            owner.post(f"/api/projects/{project_id}/resources", json=GITHUB)
            for _ in range(5)
        )
    )

    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1, f"應該恰好一個成功，實際 {codes}"
    assert codes.count(409) == 4, f"其餘都該是 409，實際 {codes}"
    assert await db.fetchval("select count(*) from project_resources") == 50


async def test_a_write_waits_for_whoever_holds_the_project_row(db, login):
    """寫入端點真的有鎖住 project 列 —— 不是只有「看起來像」。

    另一條連線先把那一列鎖住，這時候發出的 POST 必須**等**。等不到就代表
    寫入路徑沒有拿鎖，那麼 close 與寫入就可能交錯（結案之後還寫得進東西）。

    做法：拿一條獨立連線開交易並 `SELECT … FOR UPDATE`，然後給 POST 一個很短
    的逾時，斷言它逾時；放開鎖之後同一個請求要成功。
    """
    project_id, owner = await active(login)

    holder = await db.acquire()
    try:
        transaction = holder.transaction()
        await transaction.start()
        await holder.fetchval(
            "select id from projects where id = $1 for update", uuid.UUID(project_id)
        )

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                owner.post(f"/api/projects/{project_id}/resources", json=GITHUB),
                timeout=1.5,
            )

        assert (
            await holder.fetchval("select count(*) from project_resources") == 0
        ), "鎖沒有擋住寫入"
        await transaction.rollback()
    finally:
        await db.release(holder)

    # 鎖放開之後，同樣的請求要成功 —— 證明上面的逾時是等鎖，不是壞掉。
    after = await owner.post(f"/api/projects/{project_id}/resources", json=GITHUB)
    assert after.status_code == 201, after.text


async def test_closing_while_writing_never_leaves_a_late_resource(db, login):
    """close 與新增同時發生：兩種順序都可以，但結案之後不能再多出東西。

    close 的 `UPDATE projects` 會跟寫入端點的鎖互相排隊，所以結果只有兩種：
    先寫成功再結案（1 筆），或先結案再被擋（0 筆、409）。
    """
    project_id, owner = await active(login)

    written, closed = await asyncio.gather(
        owner.post(f"/api/projects/{project_id}/resources", json=GITHUB),
        owner.post(f"/api/projects/{project_id}/close", json={}),
    )

    assert closed.status_code == 200, closed.text
    assert written.status_code in (201, 409), written.text
    stored = await db.fetchval("select count(*) from project_resources")
    assert stored == (1 if written.status_code == 201 else 0)

    # 結案之後，無論如何都不能再寫進來。
    late = await owner.post(f"/api/projects/{project_id}/resources", json=GITHUB)
    assert late.status_code == 409, late.text
    assert await db.fetchval("select count(*) from project_resources") == stored


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
