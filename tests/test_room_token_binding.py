"""room token 與持有人的綁定。規格書 §6.2。

`require_room_token` 不碰資料庫，所以這些測試現在就跑得起來，不必等 [D01]。

要說清楚的一件事：目前**沒有任何路徑**走得到「拿到別人的 token」這個情況
—— token 只由 POST /api/projects/{id}/enter 簽發，而且直接寫進簽發對象自己
的 session。所以下面的 403 在今天是防禦性的。

留著它的理由是：哪天有人加了「從 header 或 request body 收 room token」的
端點（例如為了讓前端把 token 傳給別的服務），少了這一行就會變成可以借用
別人的票。到那時這個測試會是唯一記得這件事的東西。
"""

import uuid

import pytest
from fastapi import HTTPException

from app import room_token
from app.deps import require_room_token


class FakeRequest:
    """require_room_token 只用到 request.session。"""

    def __init__(self, session: dict):
        self.session = session


def request_holding(project_id, token: str) -> FakeRequest:
    return FakeRequest({"room_tokens": {str(project_id): token}})


async def test_a_matching_token_is_accepted():
    project_id, me = uuid.uuid4(), uuid.uuid4()
    token = room_token.issue(str(project_id), str(me))

    result = await require_room_token(request_holding(project_id, token), project_id, me)
    assert result == project_id


async def test_someone_elses_token_is_rejected():
    """簽章合法、房間也對，但簽發對象不是我。"""
    project_id = uuid.uuid4()
    me, someone_else = uuid.uuid4(), uuid.uuid4()
    token = room_token.issue(str(project_id), str(someone_else))

    with pytest.raises(HTTPException) as exc:
        await require_room_token(request_holding(project_id, token), project_id, me)

    assert exc.value.status_code == 403
    assert "不屬於你" in exc.value.detail


async def test_a_token_for_another_room_is_rejected():
    me = uuid.uuid4()
    this_room, other_room = uuid.uuid4(), uuid.uuid4()
    token = room_token.issue(str(other_room), str(me))

    with pytest.raises(HTTPException) as exc:
        await require_room_token(request_holding(this_room, token), this_room, me)

    assert exc.value.status_code == 403
    assert "房間" in exc.value.detail


async def test_no_token_at_all_is_rejected():
    project_id, me = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(HTTPException) as exc:
        await require_room_token(FakeRequest({}), project_id, me)

    assert exc.value.status_code == 403


async def test_an_expired_token_is_rejected():
    project_id, me = uuid.uuid4(), uuid.uuid4()
    token = room_token.issue(str(project_id), str(me), ttl_seconds=-1)

    with pytest.raises(HTTPException) as exc:
        await require_room_token(request_holding(project_id, token), project_id, me)

    assert exc.value.status_code == 403


async def test_a_forged_token_is_rejected():
    project_id, me = uuid.uuid4(), uuid.uuid4()
    real = room_token.issue(str(project_id), str(me))
    forged = real.split(".")[0] + "." + "a" * 43

    with pytest.raises(HTTPException) as exc:
        await require_room_token(request_holding(project_id, forged), project_id, me)

    assert exc.value.status_code == 403


async def test_every_rejection_looks_the_same_to_the_caller():
    """四種失敗都是 403，不告訴對方是哪一種 —— 訊息可以有差別，狀態碼不行。"""
    project_id, me, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cases = [
        FakeRequest({}),
        request_holding(project_id, "垃圾"),
        request_holding(project_id, room_token.issue(str(project_id), str(other))),
        request_holding(
            project_id, room_token.issue(str(project_id), str(me), ttl_seconds=-1)
        ),
    ]
    for request in cases:
        with pytest.raises(HTTPException) as exc:
            await require_room_token(request, project_id, me)
        assert exc.value.status_code == 403
