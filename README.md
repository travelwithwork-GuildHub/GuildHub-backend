# GuildHub 後端

GuildHub 是一個以 RPG 公會為概念的多人即時協作平台，服務接案市場的媒合與協作。
**大廳**是公共空間，人與專案在這裡第一次接觸；**專案房間**有密碼保護，是成軍
之後一起工作的地方。這個 repo 是它的後端。

同一個 FastAPI process 同時提供 REST 與 WebSocket，兩層依「資料需不需要留存」
切分，而不是依功能切分：

| 層 | 管什麼 | 狀態放哪 |
|---|---|---|
| 即時層（WebSocket） | 位置座標、狀態文字、大廳／房間對話、在線人數 | 記憶體，全部不落地 |
| 持久層（REST） | 帳號、專案＝房間、座位認領、任務牆／人才牆、站內信 | PostgreSQL |

拆開的理由是量級差三個數量級：40 人同場景 × 10 Hz 的位置廣播是 15,600 則／秒，
持久層的讀寫一天只有數十到數百筆——兩者對可靠度與延遲的要求完全不同。

技術選型：Python 3.11 + FastAPI + PostgreSQL + uvicorn，部署是單一 VM +
systemd，沒有 Docker、沒有 CI（三人專案、五週，人講比機器擋快）。

前端要看的兩份東西：跑起來之後的 `/docs`（欄位的唯一真實來源），以及
[API-前端整合指南.md](API-前端整合指南.md)（`/docs` 產不出來的部分——session
流程、WS 協定、錯誤碼語意）。

---

## 跑起來

需要 **Python 3.11+**。PostgreSQL 15+ 只有持久層要用；大廳、走動、對話這些
即時層的東西不接資料庫也能跑起來。

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt         # Linux / macOS

./run.sh
```

`run.sh` 會在 `.env` 不存在時自動從 `.env.example` 複製一份，然後啟動 uvicorn。
起來之後 REST 在 `http://localhost:8000`、文件在 `/docs`、WS 在
`ws://localhost:8000/ws`。

WS 協定的權威版本是 `app/realtime/protocol.py`（可執行的那份），人看的版本在
[API-前端整合指南.md](API-前端整合指南.md) §6。

### 環境變數

`.env` 不進版控，範本是 `.env.example`。只有四個值：

| 變數 | 用途 | 預設 |
|---|---|---|
| `DATABASE_URL` | PostgreSQL 連線字串 | `postgresql://guildhub:guildhub@localhost:5432/guildhub` |
| `SESSION_SECRET` | session cookie 的簽章金鑰。**上線前務必換掉** | `change-me` |
| `ROOM_TOKEN_SECRET` | room token 的簽章金鑰，TTL 8 小時。**上線前務必換掉** | `change-me` |
| `CORS_ORIGINS` | 允許帶 cookie 跨源的前端來源，逗號分隔。不接受 `*`（見下方 CORS 說明），前後端同源時留空 | `http://localhost:5173,http://localhost:3000` |

### 資料庫

沒有 Alembic 之類的 migration 框架——一次性實例，schema 完整寫在
`sql/001_schema.sql`，用編號的 `.sql` 檔加一支套用腳本就夠：

```bash
python tools/apply_sql.py          # 只套 001_schema.sql
python tools/apply_sql.py --seed   # 連 002_seed.sql 的假資料一起
python tools/apply_sql.py --reset  # 先砍掉 public schema 再重建（會刪光資料）
```

### 測試

```bash
.venv/Scripts/python.exe -m pytest -q   # Windows
# .venv/bin/python -m pytest -q         # Linux / macOS
```

目前 174 passed、63 skipped。需要 PostgreSQL 的測試在沒有資料庫時會 skip 並
指名 `[D01]`，不會安靜通過。

### 部署

單一 VM + systemd，`deploy/` 底下就是全部：

```bash
sudo cp deploy/guildhub.service /etc/systemd/system/ && sudo systemctl enable --now guildhub
./deploy/deploy.sh          # 之後每次更新：拉程式、裝相依、跑測試、重啟、健康檢查
```

**`uvicorn` 絕對不能加 `--workers`。** 即時層的全部狀態都在記憶體裡
（`SceneRegistry`、`PresenceStore`、`Broadcaster` 都是 module-level 物件），
多一個 worker 就是多一份互相看不見的世界——大廳裡的兩個人會隨機分到不同
process、看不到彼此，而且不會有任何錯誤訊息。40 連線實測佔 60.7 MB，單一
process 綽綽有餘。

併發實測（規格書 §8 的 W1 spike）：

```bash
python tools/run_swarm.py --n 40 --seconds 60
```

```bash
python tools/run_swarm.py --n 5 --idle
```

`--idle` 驗證規格書 §3.2：靜止時 pos 封包數必須為 0。

---

## 交給 P2 的東西

**`/docs`**。就這個 URL —— 欄位層級不另寫手冊，手寫等於兩份真實來源，W2 一定
分歧，而分歧時會相信錯的那份。

另外有一份 [API-前端整合指南.md](API-前端整合指南.md)：它**不重述欄位**（那是
`/docs` 的事），只寫 `/docs` 產不出來的東西 —— session 流程、WS 協定、錯誤碼的
語意、以及幾個照 schema 寫會踩到的行為（超長欄位回 500、`read_at` 永遠是 null、
靜止時收不到 `pos`）。

**CORS 已設定。** 預設放行 `http://localhost:5173` 與 `http://localhost:3000`，
換 port 改 `.env` 的 `CORS_ORIGINS`（逗號分隔，不接受 `*`——帶 cookie 的跨源請求
瀏覽器不收萬用字元，填了會在啟動時報錯）。前端每個請求都要帶
`credentials: 'include'`，否則 session cookie 不會送出去，症狀是全部 401。

16 個端點已凍結。清單的機器可讀版本是 `tests/test_contract.py` 的 `EXPECTED`，
它會擋下任何多出來或少掉的端點；人看的版本在
[API-前端整合指南.md](API-前端整合指南.md) §5。凍結後要改欄位必須先通知 P2。

現在就能做的事：

- 讀 `/docs` 產生 client、對著 schema 寫型別
- 每個端點未登入都回 401，可據此寫攔截器
- 錯誤碼照下表，不會再變

**還不能做的事**：端點的實作尚未在真實 PostgreSQL 上跑過（等 [D01]）。
契約是穩的，行為還沒驗過。

兩個會讓人意外的地方：

- **超長欄位不會回 422，會是資料庫錯誤。** `models.py` 刻意不重複實作長度檢查
  （任務 [P15] 的裁決），長度規則的唯一來源是 `sql/001_schema.sql`
- **`password_hash` 永遠不會出現在任何回應裡**，有測試盯著

## 交給 P3 的東西

WS 協定的權威版本是 `app/realtime/protocol.py`，人看的版本在
[API-前端整合指南.md](API-前端整合指南.md) §6。
P3 只需要三個函式（整合指南 §6.5）：

```javascript
onSnapshot(players)      // 進場全量
onPositions(list)        // 每 100 ms 的差量，餵給 interpolate()
sendMove(x, y, facing)   // 自行節流至 10 Hz
```

現在就能做的事：

- `./run.sh` 之後直接連 `ws://localhost:8000/ws?scene=lobby`，**不需要登入、
  不需要資料庫**。瀏覽器連進來會是匿名身分，暱稱一律「訪客」
- `python tools/run_swarm.py --n 40 --seconds 300` 讓大廳有 40 個會走動的人，
  拿來開發插值補間
- `tests/realtime/test_browser_client.py` 就是瀏覽器連線的可執行範例

兩件必須知道的事：

- **`pos` 只送本 tick 內有移動的人**，靜止時整則訊息不送（不是送空陣列）。
  所以收不到 `pos` 是正常的，不要當成斷線
- **位置差量是陣列 `["<id>",120,340,2]`，不是物件**。40 人 × 10 Hz 下欄位名
  會被重複送 400 次／秒

**進房間需要登入。** 大廳可以匿名逛，房間不行 —— 握手時會比對 room token
的簽發對象與連線者（[R31]），匿名連線拿到的是隨機身分，對不上任何一張票。
這符合 §6.2：票是拿房間密碼向 `POST /api/projects/{id}/enter` 換來的，
本來就沒有匿名進房這條路。

流程是：`/api/login` → `/enter`（帶密碼）拿 `room_token` →
`wss://…/ws?scene=room:{project_id}&token={room_token}`，session cookie 由
瀏覽器自動帶上。

**還不能做的事**：登入需要資料庫（[D01] + [P12]），所以房間場景要等資料庫
才能端到端跑。大廳、走動、對話、狀態文字現在就能做。走廊門位 `/api/rooms`
的在線人數已經可用，但專案名稱要等資料庫。

## 錯誤碼慣例

> 每個碼的實際觸發條件寫在 `app/api/*.py` 的 docstring 裡；前端視角的說明在
> [API-前端整合指南.md](API-前端整合指南.md) §4。

| 碼 | 用在哪 |
|---|---|
| 401 | 未登入 |
| 403 | 非發起人操作 owner-only 端點；房間密碼錯誤 |
| 404 | 專案／名片不存在 |
| **409** | **座位已被佔用（由 `seats` PK 衝突觸發，不在應用層判斷）；一人佔兩格（unique 衝突）** |
| 422 | Pydantic 驗證失敗（FastAPI 預設，不必自己處理） |
| 400 | `room_ready` check 擋下的成軍請求（未附密碼） |

**409 是規格書 §4.1 的實作證據。** 寫法必須是「送出 INSERT，捕捉資料庫的唯一鍵
衝突，轉成 409」，不能是「先 SELECT 看有沒有人坐，再決定要不要 INSERT」——
後者在兩人同時按下時會兩個都成功。

---

## 唯一真實來源方向

```
sql/001_schema.sql  →  app/models.py  →  OpenAPI（自動產生）  →  P2
```

不得反向手改任何一步。這條紀律成立的話，就永遠不需要 API 手冊。

---

## 目錄

目錄結構刻意等於任務追蹤表的 Function 欄，任何一列任務都能直接對到一個檔案。

| 路徑 | Function |
|---|---|
| `app/realtime/protocol.py` | Protocol（★ 交付 P3） |
| `app/realtime/scenes.py` | SceneRegistry |
| `app/realtime/presence.py` | PresenceStore |
| `app/realtime/broadcaster.py` | Broadcaster（10 Hz tick） |
| `app/realtime/manager.py` | ConnectionManager |
| `app/room_token.py` | RoomTokenService |
| `sql/001_schema.sql` | Schema（規格書 §4 原文） |
| `tools/fake_client.py` `tools/run_swarm.py` | TestHarness |

---

## 三件最容易被善意寫錯的事

這三件事在程式裡都有測試盯著，改動前先讀完：

1. **座位認領**不得先查再寫 —— 送出 INSERT 並捕捉約束衝突（`SeatService.claim`）
2. **站內信查詢**的主體條件必須寫在 SQL 內，不得取全部再用 Python 過濾
3. **位置廣播**固定 10 Hz，且僅送本 tick 內有移動者；靜止時整則訊息不送

第 3 點有一個實際踩過的坑：只寫 `await asyncio.sleep(0.1)` 不夠。asyncio 判斷
計時器到期時會扣掉時鐘解析度（Windows 約 15.6 ms），事件迴圈忙碌時會提早醒來，
實測變成 94 ms 一次 ≈ 10.6 Hz。10 Hz 是硬上限不是目標值，所以 tick 迴圈以單調
時鐘對齊，提早醒來就補睡到真正到期。

---

## 授權

[MIT](LICENSE)。

---

## 這份 repo 之外

規格書（`GuildHub-架構與規格書v0.1.md`）、Agent 執行守則與任務追蹤表放在專案
工作區、不在這個 repo 裡。README 與程式碼裡的 `規格書 §x`、`[P23]`、`[R31]`
這類代碼就是指向那些文件的編號——讀程式不需要它們，`app/api/*.py` 與
`app/realtime/*.py` 的 docstring 已經把每個決定的理由寫在原地。
