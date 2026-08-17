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
    所以這裡不會是 422 —— 規則只有一份，寫在 schema 裡。"""
    a = await login("A")
    b = await login("B")

    with pytest.raises(Exception):
        await a.post(
            "/api/messages", json={"recipient_id": b.user_id, "body": "字" * 2001}
        )
