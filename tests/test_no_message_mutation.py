"""[P29] 站內信不可竄改、不可刪除。規格書 §4.2、§4.1 最後一列。

任務表註記：「把『不實作』變成會失敗的測試；禁止為讓此測試通過而建立端點。」

所以這裡斷言的是「這個操作做不到」，而不是「回某個特定狀態碼」。任務表寫
405，但 405 需要該路徑本身存在、只是不支援那個 method；既然禁止建立端點，
`/api/messages/{id}` 這條路徑根本不存在，FastAPI 會回 404。兩者都代表同一
件事：改不了、刪不掉。真要讓它回 405，就得先把端點蓋出來 —— 那正是這個
測試要防止的事。
"""

import uuid

import httpx
import pytest

from app.main import app

MUTATION_ATTEMPTS = [
    ("PATCH", "/api/messages/{id}"),
    ("DELETE", "/api/messages/{id}"),
    ("PUT", "/api/messages/{id}"),
]


@pytest.mark.parametrize("method,template", MUTATION_ATTEMPTS)
async def test_no_message_mutation_endpoint_exists(method, template):
    path = template.format(id=uuid.uuid4())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, path, json={"body": "改一下"})

    assert response.status_code in (404, 405), (
        f"{method} {path} 回了 {response.status_code} —— 站內信必須是 immutable"
    )


def test_no_message_mutation_model_exists():
    """連 model 都不該有 —— 有 MessageUpdate 就代表有人打算做這件事。"""
    import app.models as models

    forbidden = [n for n in dir(models) if "Update" in n and "Message" in n]
    assert not forbidden, f"不該存在的 model：{forbidden}"
