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
3. **不得建立 `sql/001_schema.sql` 之外的資料表或欄位。** schema 已定案。
   2026-09-08 因 L3 裁決加了 `profiles.login_id` / `password_hash` —— 改法是
   **直接改 001 本身**，不是開一個 003 去 ALTER：schema 的真實來源只能有一份，
   多一個檔就多一份要對齊的東西。既有的開發庫用
   `python tools/apply_sql.py --reset --seed` 重建（裡面是假資料，重建沒有損失）
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
| Docker / docker-compose | 已砍除。2026-09-08 起正式部署走 Railway，打包由平台處理；**這個 repo 裡不要出現 Dockerfile 或 compose 檔**。細節與備援方案見 [deploy/README.md](deploy/README.md) |
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
- OAuth／第三方登入——**這條沒有被翻案**，仍然不做
- 密碼重設端點、忘記密碼信、email 驗證——沒有寄信管道，做出來是走不完的路
- 任何 `/api/admin/*`

端點清單凍結在 `tests/test_contract.py` 的 `EXPECTED`，多一個少一個都會紅。
2026-09-08 的 L3 裁決加了 `POST /api/register`（第 17 個），先有裁決再改表，
不是反過來。

2026-09-16 的 BE-G12 得到授權，加了 project_resources 表與 4 個端點（第 18～21 個）；範圍與理由見該 PR。

BE-G12 同樣是先有裁決再改表，而且是**加法式**的：
既有 17 個端點的 method、path、request、response、權限、錯誤碼一個字都沒動，
`ProjectOut` 也沒有新欄位。這種例外每一次都要留下可引用的紀錄 ——
沒有紀錄的話，下一個人會把它當成違規改回去。

2026-09-19 的 BE-G35 加了 `POST /api/messages/{message_id}/read`（第 22 個）。
`MessageOut.read_at` 從第一天就在回應裡，但沒有任何端點寫得到它。
**注意這不是 `PATCH /api/messages/{id}`** —— 那個仍然在 `FORBIDDEN` 裡，
站內信 immutable 沒有被翻案；標記已讀改的是「我看過了」這個事實，不是那句話。
同樣是先有裁決再改表，同樣是加法式的。

2026-09-21 的 BE-G38 在 `GET /api/rooms` 加了查詢參數 `mine=true`（P1 裁決：
前端單一入口要把「自己所屬的房間」排在最上面）。**不是新端點**，`EXPECTED`
沒有動；不帶參數時的行為一個字都沒變。
**§6.2「不做成員制」沒有被翻案**：沒有新增成員表，「所屬」只是兩個既有事實的
聯集 —— 我是 `projects.owner_id`，或 `seats` 裡有我。只通過密碼沒坐下的不算，
因為 room token 在 session 裡、會過期，不是資料庫的事實。
日後若有人要把「進過房」也算成所屬，那才是在引進成員制，要另外裁決。

2026-09-22 的 BE-G39 加了 `DELETE /api/projects/{project_id}/seats`（第 23 個）：
**退自己的位**。規格書 §6.3「一旦入座即固定，直到專案結束」被翻案 —— demo 現場
人站起來走掉了，那一格卻永遠顯示有人、別人坐不下、他自己也換不了位，看起來就是壞掉。
翻案範圍只有這一顆：只刪 `user_id = 我` 那一列、冪等、沒有 `seat_index`、沒有換位端點。
**發起人釋放別人座位（BE-G07）沒有被翻案**，`DELETE …/seats/{seat_index}` 仍在
`FORBIDDEN`，下面「不要實作的功能」那一條照舊。
退位之後這間房會從該隊員的 `mine` 消失（BE-G38 的「所屬」看的是**現在**的座位）；
要記住「坐過」就是成員制，同上，要另外裁決。

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
  `sql/001_schema.sql`，所以超長欄位**不會回 422**（422 代表規則被搬進了
  Pydantic）。2026-09-19 [BE-G30] 起它是 **400 + `code`**，`code` 就是
  constraint 的名稱——轉碼在 `app/main.py` 的集中 handler，規則的位置沒有變。
  在那之前它是 500，因為沒有人接住 `asyncpg.CheckViolationError`
- **`ApiError`（`app/errors.py`）的 `code` 是對外契約。** 前端拿它分支，
  改名要通知前端，等同改 `tests/test_contract.py` 的 `EXPECTED`
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
