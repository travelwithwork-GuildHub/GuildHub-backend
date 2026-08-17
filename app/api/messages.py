"""MessageService。規格書 §4.2 §4.3。

★ [P28] 必須由 P1 親自 review。

規格書 §4.3：站內信是全案唯一有實質洩漏風險的資源，其查詢必須在 SQL 層帶上
主體條件，不依賴應用層過濾。

    # 錯 —— 取全部再過濾
    rows = await fetch_all_messages()
    return [r for r in rows if r.sender_id == me]

錯的版本在測試裡看起來完全正常，因為結果一樣。它的問題是：分頁會先套用在
「全部」上再過濾，所以第 0 頁可能過濾完剩兩筆；更要命的是，任何一次重構
只要漏掉那行 list comprehension，全站的私訊就一次外洩。

## 為什麼沒有 PATCH / DELETE

§4.2：站內信是平台上唯一有紀錄的通道，也是組隊談判的憑據。允許編輯或刪除
等於讓唯一可靠的東西變得不可靠。「講過的話不能反悔」在媒合情境下是正確的
社群設計，同時省掉整組編輯／版本／稽核邏輯。

tests/test_contract.py 有一個測試盯著這兩個端點不存在。
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.deps import get_current_user
from app.models import MessageCreate, MessageOut

router = APIRouter(prefix="/api/messages", tags=["messages"])

PAGE_SIZE = 20


@router.post("", response_model=MessageOut, status_code=201)
async def send_message(
    payload: MessageCreate,
    me: uuid.UUID = Depends(get_current_user),
) -> MessageOut:
    """[P27]。寄給自己由資料庫的 no_self_send check 擋下，不在這裡判斷。"""
    try:
        row = await db.pool().fetchrow(
            "insert into messages (sender_id, recipient_id, body) "
            "values ($1, $2, $3) returning *",
            me,
            payload.recipient_id,
            payload.body,
        )
    except asyncpg.CheckViolationError as exc:
        if "no_self_send" in (exc.constraint_name or ""):
            raise HTTPException(status_code=400, detail="不能寄信給自己") from exc
        raise
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(status_code=404, detail="收件人不存在") from exc

    return MessageOut(**dict(row))


@router.get("", response_model=list[MessageOut])
async def list_messages(
    page: int = 0,
    me: uuid.UUID = Depends(get_current_user),
) -> list[MessageOut]:
    """[P28]。主體條件寫在 WHERE 裡，不是取回來之後才過濾。

    §3 的原則：即時內容空間限定，非即時內容全域可達 —— 對話隔音，收件匣
    不隔音。所以這裡同時撈寄件與收件，兩者都是「我的信」。
    """
    rows = await db.pool().fetch(
        "select * from messages "
        "where (sender_id = $1 or recipient_id = $1) "
        "order by created_at desc limit $2 offset $3",
        me,
        PAGE_SIZE,
        max(page, 0) * PAGE_SIZE,
    )
    return [MessageOut(**dict(r)) for r in rows]
