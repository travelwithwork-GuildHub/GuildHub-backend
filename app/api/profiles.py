"""ProfileService。規格書 §5.3：人才看板。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.deps import get_current_user
from app.models import ProfileOut, ProfileUpdate

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

PAGE_SIZE = 20


@router.get("", response_model=list[ProfileOut])
async def list_profiles(
    page: int = 0,
    _me: uuid.UUID = Depends(get_current_user),
) -> list[ProfileOut]:
    """[P17]。只做翻頁，不做搜尋與篩選（任務表明列）。

    超過尾頁回空清單，不是 404 —— 翻到底是正常操作，不是錯誤。
    """
    rows = await db.pool().fetch(
        "select * from profiles order by updated_at desc limit $1 offset $2",
        PAGE_SIZE,
        max(page, 0) * PAGE_SIZE,
    )
    return [ProfileOut(**dict(r)) for r in rows]


@router.patch("/me", response_model=ProfileOut)
async def update_me(
    payload: ProfileUpdate,
    user_id: uuid.UUID = Depends(get_current_user),
) -> ProfileOut:
    """[P15]。未給的欄位不動。

    長度上限交給資料庫的 check 擋（守則 §1 規則 3 與任務表 [P15]：禁止在
    應用層重複實作長度檢查）。因此超長會是 500 而不是 422 —— 這是刻意的，
    真正的規則只有一份，寫在 sql/001_schema.sql。
    """
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        row = await db.pool().fetchrow("select * from profiles where id = $1", user_id)
        return ProfileOut(**dict(row))

    # 欄位名會被拼進 SQL 字串（值仍走參數化）。名稱只可能來自 ProfileUpdate
    # 宣告過的欄位，但這裡明確再擋一次 —— 日後若有人改成從 request 直接讀
    # 欄位名，這行會先炸掉，而不是變成注入點。
    allowed = set(ProfileUpdate.model_fields)
    unexpected = set(fields) - allowed
    if unexpected:
        raise HTTPException(status_code=422, detail=f"未知欄位：{sorted(unexpected)}")

    assignments = ", ".join(f"{name} = ${i + 2}" for i, name in enumerate(fields))
    row = await db.pool().fetchrow(
        f"update profiles set {assignments}, updated_at = now() "
        "where id = $1 returning *",
        user_id,
        *fields.values(),
    )
    return ProfileOut(**dict(row))


@router.get("/{profile_id}", response_model=ProfileOut)
async def get_profile(
    profile_id: uuid.UUID,
    _me: uuid.UUID = Depends(get_current_user),
) -> ProfileOut:
    row = await db.pool().fetchrow("select * from profiles where id = $1", profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="名片不存在")
    return ProfileOut(**dict(row))
