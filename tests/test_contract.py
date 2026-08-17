"""[P23] [P24] REST 契約：只建立附錄 B 列出的端點，一個不多。

這個檔案不需要資料庫 —— 它驗的是介面形狀，不是行為。因此 [D01] 完成之前
就能凍結契約交付 P2（任務表 [P24]：★★要徑解鎖點，P2 的 E2 起全部堵在此）。

附錄 B 那張表不是 API 手冊，是路由檔案的目錄。實際規格以 /docs 為準。
"""

import pytest

from app.main import app

# 附錄 B 原文。改這張表等於改對外契約 —— 凍結後要改必須通知 P2。
EXPECTED = {
    ("POST", "/api/login"),
    ("GET", "/api/me"),
    ("GET", "/api/profiles"),
    ("GET", "/api/profiles/{profile_id}"),
    ("PATCH", "/api/profiles/me"),
    ("POST", "/api/projects"),
    ("GET", "/api/projects"),
    ("GET", "/api/projects/{project_id}"),
    ("POST", "/api/projects/{project_id}/form-team"),
    ("POST", "/api/projects/{project_id}/close"),
    ("POST", "/api/projects/{project_id}/enter"),
    ("GET", "/api/projects/{project_id}/seats"),
    ("POST", "/api/projects/{project_id}/seats"),
    ("POST", "/api/messages"),
    ("GET", "/api/messages"),
    ("GET", "/api/rooms"),
}

# 明確不存在的端點（附錄 B 末段：列出以防日後有人順手補上）
FORBIDDEN = {
    ("PATCH", "/api/messages/{message_id}"),  # §4.2 站內信 immutable
    ("DELETE", "/api/messages/{message_id}"),
    ("DELETE", "/api/projects/{project_id}/seats/{seat_index}"),  # 釋放座位已砍除
    ("POST", "/api/projects/{project_id}/reset-password"),  # 密碼重設已砍除
}


def declared_routes() -> set[tuple[str, str]]:
    """從 OpenAPI schema 讀，不從 app.routes 讀。

    兩個理由：FastAPI 新版的 include_router 會把路由包在 _IncludedRouter 裡，
    app.routes 看不到個別端點；更重要的是，P2 拿到的就是這份 schema ——
    驗它才是驗真正交付出去的東西。
    """
    found = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method in operations:
            found.add((method.upper(), path))
    return found


def test_every_appendix_b_endpoint_exists():
    missing = EXPECTED - declared_routes()
    assert not missing, f"附錄 B 有、程式沒有：{sorted(missing)}"


def test_no_endpoint_beyond_appendix_b():
    """守則 §3：即使看起來『順手就能加』也不要加。"""
    extra = declared_routes() - EXPECTED
    assert not extra, f"程式有、附錄 B 沒有：{sorted(extra)}"


@pytest.mark.parametrize("method,path", sorted(FORBIDDEN))
def test_explicitly_absent_endpoints_are_absent(method, path):
    assert (method, path) not in declared_routes()


def test_no_admin_endpoints():
    """附錄 B：任何 /api/admin/* 都未定義。"""
    assert not [p for _, p in declared_routes() if p.startswith("/api/admin")]


def test_openapi_schema_renders():
    """[P24] 的交付物就是 /docs 這個 URL。它產不出來就等於沒有交付。"""
    schema = app.openapi()
    assert schema["info"]["title"] == "GuildHub"
    api_paths = {p for p in schema["paths"] if p.startswith("/api/")}
    assert api_paths == {p for _, p in EXPECTED}


def test_password_hash_is_never_exposed():
    """projects 有 password_hash 欄位，但它不該出現在任何回應 schema 裡。"""
    schemas = app.openapi()["components"]["schemas"]
    leaked = [
        name
        for name, spec in schemas.items()
        if "password_hash" in (spec.get("properties") or {})
    ]
    assert not leaked, f"這些 schema 洩漏了 password_hash：{leaked}"
