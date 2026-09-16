"""[BE-G12] Project Resources：Project Room 裡的外部連結看板。

2026-09-16 得到授權，對凍結的 schema 與端點清單做**加法式、受限**的例外：
新增一張 `project_resources` 表與這四個端點，既有 17 個端點一個字都沒動，
`ProjectOut` 也沒有新欄位。

## 誰讀得到、誰寫得動

    讀：發起人，或持有本人有效 room token 的人（也就是進得了房間的人）
    寫：**只有發起人**

room token 是「我通過了房間密碼」，不是「我可以改這個專案的持久資料」。
§6.2 沒有成員制，只有發起人與其他人 —— 把 token 當成編輯權，等於讓任何
拿到密碼的人改掉別人專案的連結。

## 為什麼這裡有 SELECT ... FOR UPDATE

`CLAUDE.md` 規則 4 說不得寫「先查詢再寫入」。那條規則防的是**用查詢代替
資料庫約束**（座位那種：查「有沒有人坐」再寫，兩個人同時查都會過）。

這裡有兩個條件寫不成 check，因為它們跨兩張表、或跨同一張表的多列：

  1. project 必須是 active 才能動它的資源（條件在 projects，寫入在 project_resources）
  2. 每個 project 最多 50 筆（跨同一張表的多列，check 看不到其他列）

先例是座位數上限：同樣跨兩張表，由 P1 裁決改在應用層（`seats.py`）。
差別是那裡查的 `seat_count` 成軍後不再變動，可以塞進單一句 INSERT ... SELECT；
這裡的 `status` 隨時可能被 `/close` 改掉，所以要鎖住那一列 project 再寫。

**鎖與計數不能擠在同一句。** PostgreSQL 預設的 Read Committed 下，同一句
SQL 的計數沿用這句開始時的快照，看不到等鎖期間別人剛提交的列 —— 上限照樣
會被突破。所以是同一個交易裡的兩句：第一句鎖，第二句才計數與寫入。
`tests/test_project_resources.py` 的兩條並行測試盯著這件事。

`/close` 端點不必改：它的 `UPDATE projects` 本來就會跟這把鎖互相排隊。

## 長度與 URL scheme 為什麼不在這裡擋

[P15]：長度規則的唯一來源是 `sql/001_schema.sql`。超長、非 http(s) 的 URL、
只有空白的標籤，都會是資料庫錯誤（對外 500），跟 profiles、messages 一致。
前端自己擋，整合指南 §8 有記。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db, room_token
from app.deps import get_current_user
from app.models import ProjectResourceCreate, ProjectResourceOut, ProjectResourceUpdate

router = APIRouter(prefix="/api/projects", tags=["project-resources"])

# 一個專案最多幾筆。看板是給人掃過去的，不是書籤匯出。
MAX_RESOURCES = 50

_COLUMNS = "id, project_id, label, type, url, created_at"


async def _holds_a_ticket(request: Request, project_id: uuid.UUID, me: uuid.UUID) -> bool:
    """這個人手上有沒有這個房間、發給他本人的有效 room token。

    跟 `deps.require_room_token` 同一套判斷，但這裡要的是 true/false：
    GET 的失敗碼取決於還有沒有別的身分成立（發起人），不能由依賴直接丟 403。
    """
    token = (request.session.get("room_tokens") or {}).get(str(project_id))
    try:
        claims = room_token.verify(token)
    except room_token.InvalidRoomToken:
        return False
    return claims.project_id == str(project_id) and claims.user_id == str(me)


async def _project(project_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    """回 (owner_id, status)；專案不存在就 404。"""
    row = await db.pool().fetchrow(
        "select owner_id, status from projects where id = $1", project_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="專案不存在")
    return row["owner_id"], row["status"]


@router.get("/{project_id}/resources", response_model=list[ProjectResourceOut])
async def list_resources(
    request: Request,
    project_id: uuid.UUID,
    me: uuid.UUID = Depends(get_current_user),
) -> list[ProjectResourceOut]:
    """清單。固定排序，不分頁（上限 50 筆，分頁只會多一個沒人用的參數）。

    還在招募的專案回空陣列給發起人：房間還沒開，沒有東西可看，但那不是錯誤。
    結案的專案只有發起人讀得到 —— **不能靠 room token 擋**，因為 `/close`
    不清 `password_hash`，結案之後照樣換得到票（前端 FE-N08 的規格也記了
    這件事）。所以這裡自己查 status。
    """
    owner_id, status = await _project(project_id)

    if owner_id != me:
        if status != "active" or not await _holds_a_ticket(request, project_id, me):
            raise HTTPException(status_code=403, detail="尚未通過房間密碼驗證")

    rows = await db.pool().fetch(
        f"select {_COLUMNS} from project_resources where project_id = $1 "
        "order by created_at asc, id asc",
        project_id,
    )
    return [ProjectResourceOut(**dict(row)) for row in rows]


async def _lock_active_project(conn, project_id: uuid.UUID, me: uuid.UUID) -> None:
    """鎖住這一列 project，或說出為什麼不能寫。

    第一句只做一件事：把「這是我的、而且現在是 active」與「鎖住它」合成一個
    動作。零列時才去做第二次查詢分辨原因 —— 那一句不在成功路徑上，也不參與
    任何競爭（請求已經失敗了）。
    """
    locked = await conn.fetchval(
        "select id from projects "
        "where id = $1 and owner_id = $2 and status = 'active' for update",
        project_id,
        me,
    )
    if locked is not None:
        return

    row = await conn.fetchrow(
        "select owner_id, status from projects where id = $1", project_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="專案不存在")
    if row["owner_id"] != me:
        raise HTTPException(status_code=403, detail="只有發起人可以做這件事")
    raise HTTPException(
        status_code=409,
        detail=(
            "專案已結案，資源不能再修改"
            if row["status"] == "closed"
            else "專案還沒成軍，還沒有房間可以放資源"
        ),
    )


@router.post(
    "/{project_id}/resources", response_model=ProjectResourceOut, status_code=201
)
async def create_resource(
    payload: ProjectResourceCreate,
    project_id: uuid.UUID,
    me: uuid.UUID = Depends(get_current_user),
) -> ProjectResourceOut:
    """新增一筆。上限 50 筆，滿了回 409。

    計數寫在 insert 的 where 裡，跟鎖是**分開的兩句**（見檔頭）。
    """
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await _lock_active_project(conn, project_id, me)

            row = await conn.fetchrow(
                "insert into project_resources (project_id, label, type, url) "
                "select $1::uuid, $2, $3, $4 "
                "where (select count(*) from project_resources where project_id = $1::uuid) < $5 "
                f"returning {_COLUMNS}",
                project_id,
                payload.label,
                payload.type.value,
                payload.url,
                MAX_RESOURCES,
            )

    if row is None:
        raise HTTPException(
            status_code=409, detail=f"一個專案最多 {MAX_RESOURCES} 筆資源"
        )
    return ProjectResourceOut(**dict(row))


@router.patch(
    "/{project_id}/resources/{resource_id}", response_model=ProjectResourceOut
)
async def update_resource(
    payload: ProjectResourceUpdate,
    project_id: uuid.UUID,
    resource_id: uuid.UUID,
    me: uuid.UUID = Depends(get_current_user),
) -> ProjectResourceOut:
    """真正的 partial update：沒給的欄位不動，送 `{}` 回 200 原樣。

    `{}` 回 422 看起來比較嚴格，但那會讓「沒有改動就不要送」變成前端的義務，
    而 `PATCH /api/profiles/me` 早就是回 200 原樣 —— 同一個動詞在同一個 API
    裡有兩種語意，才是真正的坑。
    """
    given = payload.model_dump(include=payload.model_fields_set)

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await _lock_active_project(conn, project_id, me)

            if not given:
                row = await conn.fetchrow(
                    f"select {_COLUMNS} from project_resources "
                    "where id = $1 and project_id = $2",
                    resource_id,
                    project_id,
                )
            else:
                columns = list(given)
                assignments = ", ".join(
                    f"{name} = ${i + 3}" for i, name in enumerate(columns)
                )
                values = [
                    value.value if hasattr(value, "value") else value
                    for value in (given[name] for name in columns)
                ]
                row = await conn.fetchrow(
                    f"update project_resources set {assignments} "
                    f"where id = $1 and project_id = $2 returning {_COLUMNS}",
                    resource_id,
                    project_id,
                    *values,
                )

    if row is None:
        raise HTTPException(status_code=404, detail="資源不存在")
    return ProjectResourceOut(**dict(row))


@router.delete("/{project_id}/resources/{resource_id}", status_code=204)
async def delete_resource(
    project_id: uuid.UUID,
    resource_id: uuid.UUID,
    me: uuid.UUID = Depends(get_current_user),
) -> None:
    """硬刪除。第二次刪同一筆是 404 —— 「我剛刪掉」與「有人先刪了」對使用者
    是兩件事，前端要分得出來。"""
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await _lock_active_project(conn, project_id, me)

            deleted = await conn.fetchval(
                "delete from project_resources where id = $1 and project_id = $2 "
                "returning id",
                resource_id,
                project_id,
            )

    if deleted is None:
        raise HTTPException(status_code=404, detail="資源不存在")
