"""MessageService。規格書 §4.2 §4.3。

★ [P28] 是必審項，因為站內信是全案唯一有實質洩漏風險的資源。

其中 test_message_isolation_survives_paging 是專門用來抓錯誤寫法的：

    # 錯
    rows = await fetch_all_messages()      # limit 20 先套用在「全部」上
    return [r for r in rows if r.sender_id == me]

這個寫法在只有幾封信的測試裡結果完全正確。要讓它露出破綻，必須讓「別人的信」
多到把分頁塞滿 —— 那時正確的實作仍看得到自己的信，錯誤的實作會回空清單。
"""

import uuid

import pytest

pytestmark = pytest.mark.db


async def test_message_isolation_third_party_cannot_see_it(db, login):
    """[P25] A 寄給 B 之後，C 呼叫 list 看不到這封。"""
    a = await login("寄件人A")
    b = await login("收件人B")
    c = await login("路人C")

    sent = await a.post(
        "/api/messages", json={"recipient_id": b.user_id, "body": "只給 B 看的內容"}
    )
    assert sent.status_code == 201, sent.text

    for viewer, should_see in ((a, True), (b, True), (c, False)):
        listed = await viewer.get("/api/messages")
        assert listed.status_code == 200
        bodies = [m["body"] for m in listed.json()]
        assert ("只給 B 看的內容" in bodies) is should_see


async def test_message_isolation_survives_paging(db, login):
    """把別人的信塞滿一整頁，自己的信仍然找得到。

    這是「取全部再過濾」與「條件寫在 SQL 內」唯一會給出不同答案的情境。
    """
    noisy = [await login(f"雜訊{i}") for i in range(2)]
    for i in range(30):
        response = await noisy[0].post(
            "/api/messages",
            json={"recipient_id": noisy[1].user_id, "body": f"與我無關的第 {i} 封"},
        )
        assert response.status_code == 201

    a = await login("寄件人A")
    b = await login("收件人B")
    await a.post("/api/messages", json={"recipient_id": b.user_id, "body": "我的信"})

    listed = await b.get("/api/messages")
    assert listed.status_code == 200
    bodies = [m["body"] for m in listed.json()]
    assert bodies == ["我的信"], (
        f"B 只該看到自己那一封，實際看到 {len(bodies)} 封。"
        "若為空清單，很可能是先取全部再用 Python 過濾。"
    )


async def test_message_isolation_inbox_is_not_soundproofed(db, login):
    """規格書 §3 原則：即時內容空間限定，非即時內容全域可達。

    對話隔音，收件匣不隔音 —— 寄件與收件都算「我的信」。
    """
    a = await login("A")
    b = await login("B")
    await a.post("/api/messages", json={"recipient_id": b.user_id, "body": "去信"})
    await b.post("/api/messages", json={"recipient_id": a.user_id, "body": "回信"})

    listed = await a.get("/api/messages")
    bodies = {m["body"] for m in listed.json()}
    assert bodies == {"去信", "回信"}


async def test_self_send_is_blocked_by_the_database(db, login):
    """[P26]。必須是資料庫的 no_self_send check 擋下，不是應用層的 if。"""
    me = await login("自言自語的")
    response = await me.post(
        "/api/messages", json={"recipient_id": me.user_id, "body": "喂"}
    )
    assert response.status_code == 400

    count = await db.fetchval("select count(*) from messages")
    assert count == 0


async def test_self_send_constraint_exists_in_the_schema(db):
    """直接問資料庫有沒有這條約束 —— 應用層擋掉不算數。"""
    exists = await db.fetchval(
        "select count(*) from pg_constraint where conname = 'no_self_send'"
    )
    assert exists == 1


async def test_message_to_a_stranger_that_does_not_exist_is_404(db, login):
    me = await login("寄給幽靈的")
    response = await me.post(
        "/api/messages", json={"recipient_id": str(uuid.uuid4()), "body": "有人嗎"}
    )
    assert response.status_code == 404


async def test_messages_are_newest_first(db, login):
    a = await login("A")
    b = await login("B")
    for i in range(3):
        await a.post("/api/messages", json={"recipient_id": b.user_id, "body": f"第{i}封"})

    listed = await b.get("/api/messages")
    assert [m["body"] for m in listed.json()] == ["第2封", "第1封", "第0封"]


async def test_message_body_length_is_enforced_by_the_database(db, login):
    """§4 的 check (1..2000)。models.py 刻意不重複實作長度檢查（[P15]），
    所以這裡不會是 422 —— 規則只有一份，寫在 schema 裡。

    ## 2026-09-19 [BE-G30] 裁決破例：改了這條已經綠的測試

    與 tests/test_profiles.py 的 test_profile_overlong_field_is_rejected_by_the_database
    同一次破例、同一個理由（絕對規則 2 的唯一例外，兩條都在那個 commit 裡）。

    以前沒有人接住 asyncpg.CheckViolationError —— send_message 只接了
    no_self_send，其他往外噴，所以超長站內信是 500。現在集中的 handler 轉成
    400 + `code`。**規則仍然只寫在 sql/001_schema.sql**，仍然不是 422
    （422 代表規則被搬進了 Pydantic，那是另一回事）。
    """
    a = await login("A")
    b = await login("B")

    response = await a.post(
        "/api/messages", json={"recipient_id": b.user_id, "body": "字" * 2001}
    )

    assert response.status_code == 400, f"回了 {response.status_code}：{response.text}"
    assert response.json()["code"] == "messages_body_check"
    assert response.status_code != 422

    assert await db.fetchval("select count(*) from messages") == 0, "超長的信不該被寫進去"


# ---------------------------------------------------- [BE-G35] 標記已讀
#
# 前端 2026-09-19 的清單第 2.3 條：MessageOut.read_at 有回，但**沒有任何端點
# 寫得到它** —— 它永遠是 null，所以收件匣做不了未讀。
#
# 端點是 POST /api/messages/{message_id}/read，不是 PATCH /api/messages/{id}：
# 站內信 immutable（§4.2），test_contract.py 的 FORBIDDEN 釘著 PATCH 與 DELETE
# 不存在，那一條沒有被翻案。標記已讀不是編輯信件內容。
#
# 這是合併 be-g12 之後的第 22 個端點，所以 test_contract.py 的 EXPECTED 與
# test_auth_gate.py 的 PROTECTED 同時各加一列 —— 契約有三份紀錄（還有
# API-前端整合指南.md），只改一份就是下一次「前端說有、後端說沒有」的來源。


async def test_marking_a_message_read_sets_read_at(db, login):
    sender = await login("寄信的")
    recipient = await login("收信的")
    sent = await sender.post(
        "/api/messages", json={"recipient_id": recipient.user_id, "body": "在嗎"}
    )
    message_id = sent.json()["id"]
    assert sent.json()["read_at"] is None

    response = await recipient.post(f"/api/messages/{message_id}/read")

    assert response.status_code == 200, response.text
    assert response.json()["read_at"] is not None
    assert response.json()["id"] == message_id
    assert response.json()["body"] == "在嗎", "標記已讀不得動到信件內容"


async def test_marking_read_twice_keeps_the_first_timestamp(db, login):
    """冪等。收件匣開兩個分頁、或重新整理，不該讓已讀時間一直往前跳。"""
    sender = await login("寄信的")
    recipient = await login("收信的")
    sent = await sender.post(
        "/api/messages", json={"recipient_id": recipient.user_id, "body": "在嗎"}
    )
    message_id = sent.json()["id"]

    first = await recipient.post(f"/api/messages/{message_id}/read")
    second = await recipient.post(f"/api/messages/{message_id}/read")

    assert second.status_code == 200, second.text
    assert second.json()["read_at"] == first.json()["read_at"]


async def test_only_the_recipient_can_mark_a_message_read(db, login):
    """寄件人不能替收件人把信標成已讀 —— 那是在偽造對方讀過的證據。"""
    sender = await login("寄信的")
    recipient = await login("收信的")
    sent = await sender.post(
        "/api/messages", json={"recipient_id": recipient.user_id, "body": "在嗎"}
    )
    message_id = sent.json()["id"]

    response = await sender.post(f"/api/messages/{message_id}/read")

    assert response.status_code == 403, f"回了 {response.status_code}：{response.text}"
    assert response.json()["code"] == "not_recipient"

    still = await db.fetchval(
        "select read_at from messages where id = $1", uuid.UUID(message_id)
    )
    assert still is None


async def test_a_stranger_cannot_mark_a_message_read(db, login):
    """跟 §4.3 同一個道理：主體條件寫在 SQL 的 where 裡，不是取回來再比。"""
    sender = await login("寄信的")
    recipient = await login("收信的")
    stranger = await login("路人")
    sent = await sender.post(
        "/api/messages", json={"recipient_id": recipient.user_id, "body": "在嗎"}
    )

    response = await stranger.post(f"/api/messages/{sent.json()['id']}/read")

    assert response.status_code == 403
    assert response.json()["code"] == "not_recipient"


async def test_marking_a_missing_message_is_404(db, login):
    me = await login("收信的")

    response = await me.post(f"/api/messages/{uuid.uuid4()}/read")

    assert response.status_code == 404
    assert response.json()["code"] == "message_not_found"


async def test_read_at_shows_up_in_the_inbox_afterwards(db, login):
    """驗收的是整條路：標記完之後，收件匣列表也要看得到。"""
    sender = await login("寄信的")
    recipient = await login("收信的")
    unread = await sender.post(
        "/api/messages", json={"recipient_id": recipient.user_id, "body": "第一封"}
    )
    read = await sender.post(
        "/api/messages", json={"recipient_id": recipient.user_id, "body": "第二封"}
    )
    await recipient.post(f"/api/messages/{read.json()['id']}/read")

    inbox = {m["id"]: m["read_at"] for m in (await recipient.get("/api/messages")).json()}

    assert inbox[read.json()["id"]] is not None
    assert inbox[unread.json()["id"]] is None
