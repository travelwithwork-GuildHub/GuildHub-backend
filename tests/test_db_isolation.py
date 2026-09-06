"""測試不得砍到開發用的資料庫。

真實事故的形狀：`tests/conftest.py` 的 `db` fixture 對它連到的庫下
`drop schema public cascade`，而它連到的是 `.env` 裡開發用的那一個。
症狀是**開發資料悄悄消失，而整套測試全綠** —— 沒有任何東西會提醒你。

這個檔案盯的是那條路徑上唯一脆弱的環節：`app.config` 在測試過程中會被
`importlib.reload()`（`test_cors.py` 為了驗「CORS_ORIGINS=* 要在啟動時炸掉」
必須這樣做），而 reload 會整個重跑 `app/config.py`。所以覆蓋位址不能改在
模組屬性上，要改在 `os.environ` 那一層。
"""

import importlib
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from app import config

ROOT = Path(__file__).resolve().parent.parent
DEV_DATABASE_URL = dotenv_values(ROOT / ".env").get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="沒有設 TEST_DATABASE_URL，本來就不會跑到需要資料庫的測試",
)


def test_config_points_at_the_test_database():
    """一般情況下，app 看到的位址就是測試庫。"""
    assert config.DATABASE_URL == os.environ["TEST_DATABASE_URL"]
    assert config.DATABASE_URL != DEV_DATABASE_URL


def test_reloading_config_does_not_fall_back_to_the_dev_database():
    """reload 之後也還是測試庫 —— 這是實際發生過的那個 bug。

    `config.DATABASE_URL = ...` 這種改法過不了這一條：reload 會把它打回
    `.env` 的值。覆蓋必須發生在 `os.environ`，而且在 import app 之前。
    """
    try:
        importlib.reload(config)
        assert config.DATABASE_URL == os.environ["TEST_DATABASE_URL"]
        assert config.DATABASE_URL != DEV_DATABASE_URL
    finally:
        importlib.reload(config)  # 還原給其他測試用


def test_dev_and_test_urls_are_actually_different():
    """兩者設成同一個值等於沒有隔離。conftest 會在收集階段就擋下，這裡是明說。"""
    assert os.environ["TEST_DATABASE_URL"] != DEV_DATABASE_URL
