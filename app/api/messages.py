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
from fastapi import APIRouter, Depends

from app import db
from app.errors import ApiError
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
            raise ApiError(400, "no_self_send", "不能寄信給自己") from exc
        raise
    except asyncpg.ForeignKeyViolationError as exc:
        raise ApiError(404, "recipient_not_found", "收件人不存在") from exc

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


@router.post("/{message_id}/read", response_model=MessageOut)
async def mark_read(
    message_id: uuid.UUID,
    me: uuid.UUID = Depends(get_current_user),
) -> MessageOut:
    """[BE-G35] 標記已讀（2026-09-19，前端清單 2.3）。

    MessageOut.read_at 從第一天就在回應裡，但沒有任何端點寫得到它 —— 它永遠
    是 null，所以收件匣做不了未讀。

    ## 為什麼是 POST .../read 而不是 PATCH /api/messages/{id}

    §4.2 站內信 immutable，test_contract.py 的 FORBIDDEN 釘著 PATCH 與 DELETE
    不存在，那一條沒有被翻案。標記已讀不是編輯信件內容 —— 它改的是「我看過
    了」這個事實，不是那句話本身。

    ## 冪等：coalesce 而不是先查再寫

    收件匣開兩個分頁、或重新整理，不該讓已讀時間一直往前跳。用
    `read_at = coalesce(read_at, now())` 讓資料庫決定：已經有值就保留。
    先查再寫在這裡雖然不會造成資料損壞，但會多一次來回，而且是同一個壞習慣
    （守則 §1 規則 4）。

    ## 主體條件寫在 WHERE 裡

    跟 [P28] 同一個道理（§4.3：站內信是全案唯一有實質洩漏風險的資源）——
    recipient_id = me 是 update 的條件，不是取回來之後才比。寄件人不能替
    收件人標記已讀：那是在偽造對方讀過的證據。
    """
    row = await db.pool().fetchrow(
        "update messages set read_at = coalesce(read_at, now()) "
        "where id = $1 and recipient_id = $2 returning *",
        message_id,
        me,
    )
    if row is None:
        # 沒命中，兩種可能。這一句只在請求已經失敗之後才跑，不在成功路徑上。
        exists = await db.pool().fetchval(
            "select true from messages where id = $1", message_id
        )
        if not exists:
            raise ApiError(404, "message_not_found", "訊息不存在")
        raise ApiError(403, "not_recipient", "只有收件人可以標記已讀")

    return MessageOut(**dict(row))
