"""跨站 cookie。前端在別的網域上時，session cookie 送不送得過去。

## 為什麼要有這組測試

這是這個專案部署時最貴的一種錯：CORS 設對了、部署成功了、登入也回 200 ——
然後**每一個 API 都 401**，而前後端會各自先懷疑對方。

原因是 SameSite。`SessionMiddleware` 的預設是 `Lax`，而 Lax 的 cookie 在
**跨站**請求裡瀏覽器不會送出。前端在 Vercel／GitHub Pages、後端在別的網域，
就是跨站。

所以三件事必須被釘住：
  1. 預設（同源開發）仍然是 Lax，而且不強制 Secure —— 本機沒有 HTTPS
  2. 開啟跨站模式時，`SameSite=None` 與 `Secure` **一起**翻，不能只翻一半
     （None 沒有 Secure 會被瀏覽器直接丟掉）
  3. CORS 列了非本機來源卻忘了開跨站模式 —— **啟動就炸掉**，不要等到發表日
"""

import importlib
import os

import pytest

from app import config

pytestmark = pytest.mark.db


WATCHED = ("COOKIE_CROSS_SITE", "SESSION_COOKIE_SAME_SITE", "SESSION_COOKIE_HTTPS_ONLY")


def config_with(**env: str) -> dict:
    """在指定的環境變數下重載 config，回傳**快照**，然後還原。

    回傳 dict 而不是 module，是因為 module 是單例：finally 裡的還原會重載
    同一個物件，呼叫端拿到的就會是還原後的值 —— 斷言全部對著原始設定跑，
    而且看起來像設定沒生效。這裡踩過一次，所以留著這段說明。
    """
    original = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        importlib.reload(config)
        return {name: getattr(config, name) for name in WATCHED}
    finally:
        for k, v in original.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(config)  # 還原給其他測試用


def test_default_is_lax_and_not_secure():
    """本機開發沒有 HTTPS，強制 Secure 會讓 cookie 整個不見。"""
    assert config.SESSION_COOKIE_SAME_SITE == "lax"
    assert config.SESSION_COOKIE_HTTPS_ONLY is False


def test_cross_site_flips_both_together():
    """SameSite=None 沒有 Secure 會被瀏覽器丟掉，所以兩者綁在同一個開關。"""
    snapshot = config_with(
        COOKIE_CROSS_SITE="true",
        CORS_ORIGINS="https://guildhub.vercel.app",
    )

    assert snapshot["SESSION_COOKIE_SAME_SITE"] == "none"
    assert snapshot["SESSION_COOKIE_HTTPS_ONLY"] is True


@pytest.mark.parametrize(
    "value", ["true", "TRUE", "1", "yes", "on"], ids=lambda v: f"{v!r}"
)
def test_the_flag_accepts_the_obvious_spellings(value):
    """設定檔是人手寫的。只認 "true" 的話，寫 "1" 的人會得到一個安靜的錯誤。"""
    snapshot = config_with(
        COOKIE_CROSS_SITE=value, CORS_ORIGINS="https://guildhub.vercel.app"
    )
    assert snapshot["COOKIE_CROSS_SITE"] is True


def test_a_remote_origin_without_the_flag_fails_loudly():
    """CORS 有外部來源＝前端在別的網域＝跨站。這時候沒開旗標就是漏了。

    不炸掉的話，症狀要到部署完、前端接上去、使用者登入之後才會出現，
    而那時候看起來像後端壞了。
    """
    with pytest.raises(ValueError, match="COOKIE_CROSS_SITE"):
        config_with(
            CORS_ORIGINS="https://guildhub.vercel.app", COOKIE_CROSS_SITE=""
        )


def test_localhost_origins_do_not_trigger_the_check():
    """本機開發是同站，不該被這條檢查擋住 —— 擋住的話沒有人跑得起來。"""
    snapshot = config_with(
        CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:3000",
        COOKIE_CROSS_SITE="",
    )
    assert snapshot["COOKIE_CROSS_SITE"] is False


async def test_the_login_cookie_really_carries_samesite(api):
    """不只驗設定值，也驗真的簽出來的那一個 header。

    設定對但 middleware 沒吃到，是另一種安靜的失敗。
    """
    response = await api.post("/api/login", json={"nickname": "阿凱"})

    cookie = response.headers["set-cookie"].lower()
    assert "samesite=lax" in cookie
    assert "httponly" in cookie
