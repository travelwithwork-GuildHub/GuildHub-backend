"""[BE-G34] 「我的案件」：GET /api/projects 的 owner_id 篩選與總數。

前端 2026-09-19 的清單第 2.2 條：他們的 FE-J03 要列出「我發的所有案子」，
含 active 與 closed。現在只能用 status 篩（預設 recruiting）、每頁 20 筆、
沒有總數 —— 所以他們得對三種 status 各翻多頁，再在前端過濾 owner_id，
資料一多就慢又不準。

## 為什麼回應形狀不變

前端原本要的是 `{"items": [...], "total": n, "has_more": bool}`。不採用：
GET /api/projects 同時餵著任務看板與走廊的門，形狀一改兩邊必須同一天上線，
而發表前兩邊各只剩一個部署窗口。改成把總數放 `X-Total-Count`，回應仍然是
list —— 前端的契約 schema 一行都不用改，要用總數的時候再讀 header。

`has_more` 不另外回：`(page + 1) * PAGE_SIZE < total` 就是它，多一個欄位就多
一種會跟 total 互相矛盾的可能。
"""

import uuid

import pytest

from app.api.projects import PAGE_SIZE

pytestmark = pytest.mark.db

PASSWORD = "guild1234"


async def new_project(client, **overrides) -> str:
    payload = {"title": "測試專案", "body": "內文"} | overrides
    response = await client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def expire(pool, project_id: str) -> None:
    """把貼文推到過期。§5.4 的「自動下架」是查詢時過濾的結果，不是排程。"""
    await pool.execute(
        "update projects set expires_at = now() - interval '1 day' where id = $1",
        uuid.UUID(project_id),
    )


def ids(response) -> list[str]:
    return [p["id"] for p in response.json()]


# ------------------------------------------------------------------ 哨兵
#
# 這兩條不是新功能，是**防止改壞既有契約**。前端第 3 節把它們釘成契約：
# 「GET /api/projects 不帶 status 只回 recruiting；?status=active 才回成軍的」。
# 加 owner_id 與 status=all 的時候最容易順手把預設值一起改掉。


async def test_the_default_is_still_recruiting_only(db, login):
    owner = await login("發起人")
    recruiting = await new_project(owner, title="還在找人")
    formed = await new_project(owner, title="已經成軍")
    await owner.post(f"/api/projects/{formed}/form-team", json={"password": PASSWORD})

    listed = await owner.get("/api/projects")

    assert listed.status_code == 200
    assert recruiting in ids(listed)
    assert formed not in ids(listed), "預設值不得因為新增 status=all 就被改掉"


async def test_status_active_still_returns_formed_projects(db, login):
    owner = await login("發起人")
    formed = await new_project(owner)
    await owner.post(f"/api/projects/{formed}/form-team", json={"password": PASSWORD})

    listed = await owner.get("/api/projects", params={"status": "active"})

    assert ids(listed) == [formed]


# --------------------------------------------------------------- owner_id


async def test_owner_id_returns_every_status_of_my_own_projects(db, login):
    """FE-J03 要的東西：一次請求就拿到我的全部，不必對三種 status 各翻一遍。"""
    me = await login("發起人")
    recruiting = await new_project(me, title="還在找人")
    active = await new_project(me, title="已經成軍")
    closed = await new_project(me, title="已經結案")
    for project_id in (active, closed):
        await me.post(f"/api/projects/{project_id}/form-team", json={"password": PASSWORD})
    await me.post(f"/api/projects/{closed}/close", json={})

    listed = await me.get("/api/projects", params={"owner_id": me.user_id, "status": "all"})

    assert listed.status_code == 200
    assert set(ids(listed)) == {recruiting, active, closed}


async def test_owner_id_excludes_other_peoples_projects(db, login):
    me = await login("發起人")
    mine = await new_project(me)
    someone_else = await login("別人")
    theirs = await new_project(someone_else)

    listed = await me.get("/api/projects", params={"owner_id": me.user_id, "status": "all"})

    assert mine in ids(listed)
    assert theirs not in ids(listed)


async def test_owner_id_combines_with_status(db, login):
    """兩個條件要同時成立。

    這裡一定要有「別人的 active 案子」：少了它，未實作 owner_id 的版本也會
    通過 —— FastAPI 對不認得的 query 參數是直接忽略的，回來的清單剛好只有
    自己那一筆，斷言就假綠了。
    """
    me = await login("發起人")
    recruiting = await new_project(me)
    active = await new_project(me)
    await me.post(f"/api/projects/{active}/form-team", json={"password": PASSWORD})

    someone_else = await login("別人")
    theirs = await new_project(someone_else)
    await someone_else.post(
        f"/api/projects/{theirs}/form-team", json={"password": PASSWORD}
    )

    listed = await me.get(
        "/api/projects", params={"owner_id": me.user_id, "status": "active"}
    )

    assert ids(listed) == [active]
    assert recruiting not in ids(listed), "status 沒有生效"
    assert theirs not in ids(listed), "owner_id 沒有生效"


async def test_my_own_expired_projects_are_still_mine(db, login):
    """查自己的案子時不套 expires_at 過濾。

    否則「我的案件」會在貼文過期的那一刻把案子藏起來 —— 對看板來說「過期
    就下架」是對的，對發起人自己的清單來說那是把他的東西弄丟。
    """
    me = await login("發起人")
    fresh = await new_project(me, title="還沒過期")
    stale = await new_project(me, title="已經過期")
    await expire(db, stale)

    mine = await me.get("/api/projects", params={"owner_id": me.user_id, "status": "all"})

    assert set(ids(mine)) == {fresh, stale}


async def test_other_peoples_expired_projects_stay_hidden(db, login):
    """但別人查我的案子時，過期的仍然不出現 —— 下架就是下架。"""
    me = await login("發起人")
    stale = await new_project(me)
    await expire(db, stale)

    onlooker = await login("路人")
    listed = await onlooker.get(
        "/api/projects", params={"owner_id": me.user_id, "status": "all"}
    )

    assert stale not in ids(listed)


async def test_the_board_never_shows_expired_posts(db, login):
    """§5.4 沒有變：不帶 owner_id 的看板仍然過濾過期的。"""
    me = await login("發起人")
    stale = await new_project(me)
    await expire(db, stale)

    listed = await me.get("/api/projects")

    assert stale not in ids(listed)


# ------------------------------------------------------------ X-Total-Count


async def test_total_count_counts_everything_not_just_this_page(db, login):
    me = await login("多產的")
    for i in range(PAGE_SIZE + 3):
        await new_project(me, title=f"案子 {i}")

    first = await me.get("/api/projects")

    assert len(first.json()) == PAGE_SIZE, "一頁仍然是 20 筆"
    assert first.headers["X-Total-Count"] == str(PAGE_SIZE + 3)


async def test_total_count_respects_the_same_filters(db, login):
    """總數與清單必須用同一組 where，否則 has_more 會算錯。"""
    me = await login("發起人")
    recruiting = await new_project(me)
    active = await new_project(me)
    await me.post(f"/api/projects/{active}/form-team", json={"password": PASSWORD})
    someone_else = await login("別人")
    await new_project(someone_else)

    listed = await me.get(
        "/api/projects", params={"owner_id": me.user_id, "status": "all"}
    )

    assert listed.headers["X-Total-Count"] == "2"
    assert set(ids(listed)) == {recruiting, active}


async def test_total_count_is_zero_when_nothing_matches(db, login):
    me = await login("什麼都沒發的")

    listed = await me.get("/api/projects", params={"owner_id": me.user_id, "status": "all"})

    assert listed.json() == []
    assert listed.headers["X-Total-Count"] == "0"


async def test_total_count_is_exposed_to_the_browser(db, login):
    """跨源時瀏覽器讀不到未列在 expose_headers 裡的 header。

    正式站前後端同源，所以這一條在正式站上不影響任何事；它擋的是前端
    `next dev -p 3100` 打本機 8000 的情況 —— 那時 X-Total-Count 會存在於
    回應裡，但 JavaScript 讀到 undefined，症狀是「後端說有、前端說沒有」。
    """
    from app import config

    me = await login("發起人")
    await new_project(me)

    listed = await me.get("/api/projects", headers={"Origin": config.CORS_ORIGINS[0]})

    exposed = listed.headers.get("access-control-expose-headers", "")
    assert "x-total-count" in exposed.lower(), f"expose_headers 是 {exposed!r}"


# ---------------------------------------------------------------- 排序穩定


async def test_paging_does_not_skip_or_repeat_rows(db, login):
    """排序補第二鍵（updated_at desc, id desc）。

    updated_at 是 now() 的預設值，同一批建立的案子時間戳會相同；只用
    updated_at 排序時，PostgreSQL 對相同鍵的順序不保證穩定，翻頁就會有筆
    重複出現、有筆永遠看不到。資料量小的時候幾乎不會發作，所以這裡把
    整批的 updated_at 直接壓成同一個值，強迫它發作。
    """
    me = await login("多產的")
    created = [await new_project(me, title=f"案子 {i}") for i in range(PAGE_SIZE + 5)]
    await db.execute("update projects set updated_at = now()")

    first = await me.get("/api/projects", params={"page": 0})
    second = await me.get("/api/projects", params={"page": 1})

    assert len(first.json()) == PAGE_SIZE
    assert len(second.json()) == 5

    # 只斷言「沒有重複也沒有遺漏」是不夠的 —— 沒有第二鍵的版本在資料量小、
    # 剛插完還沒被 vacuum 動過的時候，回傳順序往往剛好穩定，測試會假綠。
    # 所以直接釘死順序本身：updated_at 全部相同時，唯一的排序依據就是
    # id desc。
    seen = ids(first) + ids(second)
    assert seen == sorted(created, reverse=True), "排序沒有第二鍵，翻頁不穩定"
