# CLAUDE.md

給在這個 repo 上工作的人與 Agent。規格書裡有大量**反直覺的決定**，它們不是疏漏，
是評估後刻意排除的。問題在於這些決定違背一般專案的常見寫法，所以「照常識做對的
事」在這個專案裡經常就是錯的——這份文件的存在，是為了讓那些決定不會被善意地推翻。

**這裡的規則與你的判斷衝突時，以這裡為準；如果你認為規則有誤，停下來提出，
不要自行決定。**

專案背景與跑法在 [README.md](README.md)，前端契約在
[API-前端整合指南.md](API-前端整合指南.md)。

---

## 絕對規則

1. **唯一真實來源方向**：`sql/001_schema.sql` → `app/models.py` → OpenAPI（自動
   產生）→ 前端。**不得反向手改任何一步。** 這條紀律成立的話，就永遠不需要 API 手冊
2. **不得修改已通過的測試來讓新程式通過。** 測試紅了就改實作
3. **不得建立 `sql/001_schema.sql` 之外的資料表或欄位。** schema 已定案
4. **不得寫「先查詢再寫入」的併發邏輯。** 一律寫入後捕捉資料庫約束衝突，轉成 HTTP 狀態碼
5. **即時層的任何資料不得寫入資料庫。** 位置、狀態文字、對話、在線名單全部只在記憶體
6. **不得新增抽象層。** 不要 repository pattern、不要 plugin 機制、不要為未來保留
   的設定開關
7. **遇到規格未定義的情況：停下來提問。** 不要自行補完
8. 一個任務一個 commit，訊息開頭帶任務編號，例如 `[P23] 實作 REST 路由組裝`

---

## 不要引入的技術

| 不要 | 為什麼 |
|---|---|
| Docker / docker-compose | 已砍除，直接 VM + systemd（`deploy/`） |
| Alembic 或任何 migration 框架 | 一次性實例，用編號的 `.sql` 檔 |
| Redis | 狀態量小，記憶體足夠 |
| Celery / APScheduler 等排程器 | 貼文時效自動化已砍除 |
| ORM 定義 schema（SQLAlchemy declarative 等） | 約束必須寫在 SQL，不能寫在 Python |
| 任何前端框架 | 後端不出前端 |

ORM 當**查詢工具**可以用；當 **schema 定義來源**不行。

---

## 不要實作的功能

「順手就能加」也不要加。這些是明確排除的，不是還沒做：

- `PATCH` / `DELETE /api/messages/{id}`、站內信的編輯／版本／稽核——站內信 immutable
- 可保存的聊天紀錄、chat scrollback
- 貼文到期提醒、續期、自動下架排程
- 房間密碼重設端點、發起人釋放座位端點
- 訪客唯讀模式與相關 gate
- 帳號刪除、資料匯出
- **工時統計、在線時數、任何形式的活動追蹤——永久不做**
- 點數、金流、置頂、付費曝光
- 多樓層、房間裝飾、外觀客製
- OAuth／第三方登入——匿名暱稱登入即可
- 任何 `/api/admin/*`

端點清單凍結在 `tests/test_contract.py` 的 `EXPECTED`，多一個少一個都會紅。

---

## 四個最容易被善意寫錯的地方

### 1. 座位認領（`app/api/seats.py`）

```python
# 錯 —— 兩人同時按下時兩個都會成功
if not await seat_taken(project_id, seat_index):
    await insert_seat(...)

# 對 —— 讓資料庫的 primary key 擋下，捕捉衝突轉成 409
try:
    await insert_seat(...)
except UniqueViolation:
    raise HTTPException(409)
```

不變式寫在資料庫，因為應用層會有 bug，約束不會。409 就是這條規則的實作證據。

### 2. 站內信查詢（`app/api/messages.py`）

```python
# 錯 —— 全案唯一有實質洩漏風險的地方
rows = await fetch_all_messages()
return [r for r in rows if r.sender_id == me]

# 對 —— 主體條件寫在 SQL 內
WHERE (sender_id = :me OR recipient_id = :me)
```

### 3. 位置廣播節流（`app/realtime/broadcaster.py`）

固定 10 Hz，且**僅送本 tick 內有移動者**；靜止時整則訊息不送（不是送空陣列）。

這裡有一個實際踩過的坑：只寫 `await asyncio.sleep(0.1)` 不夠。asyncio 判斷計時器
到期時會扣掉時鐘解析度（Windows 約 15.6 ms），事件迴圈忙碌時會提早醒來，實測變成
94 ms 一次 ≈ 10.6 Hz。10 Hz 是**硬上限不是目標值**，所以 tick 迴圈以單調時鐘對齊，
提早醒來就補睡到真正到期。

### 4. 即時對話（`app/realtime/broadcaster.py`）

不要建 `chat_messages` 資料表，不要寫入任何持久化儲存，不要實作 scrollback。
重新整理後看到空白是**正確行為**——設計意圖就是讓使用者不會把重要決議留在這裡。

---

## 另外兩件事

- **不要在 `models.py` 重複實作長度檢查。** 長度規則的唯一來源是
  `sql/001_schema.sql`，所以超長欄位不會回 422，會是資料庫錯誤
- **`uvicorn` 不能加 `--workers`。** 即時層的狀態全在 module-level 物件裡，多一個
  worker 就是多一份互相看不見的世界，而且不會有任何錯誤訊息

---

## 一個任務算完成的條件

```bash
.venv/Scripts/python.exe -m pytest -q   # Windows
# .venv/bin/python -m pytest -q         # Linux / macOS
```

1. 該任務對應的測試由紅轉綠
2. 先前所有測試仍然綠，且**沒有修改任何既有測試**
3. 沒有引入上面「不要引入的技術」
4. 沒有新增上面「不要實作的功能」
5. commit 訊息帶任務編號

任一項不成立，任務未完成。
