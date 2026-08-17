# GuildHub 後端

> 規格：[GuildHub-架構與規格書v0.1.md](../GuildHub-架構與規格書v0.1.md)
> 守則：[Agent執行守則-後端.md](../Agent執行守則-後端.md)
> 任務表：[後端任務表-Agent.csv](../後端任務表-Agent.csv)

同一個 FastAPI process 同時提供 REST 與 WebSocket（規格書 §2.2）。

---

## 跑起來

```bash
./run.sh
```

`/docs` 是 REST 的唯一真實來源（自動產生，不另寫 API 手冊）。
WS 協定手寫在 [開工前準備-後端.md](../開工前準備-後端.md) 附錄 A，程式化版本在
`app/realtime/protocol.py`。

測試：

```bash
.venv/Scripts/python.exe -m pytest -q
```

需要 PostgreSQL 的測試在沒有資料庫時會 skip 並指名 `[D01]`，不會安靜通過。

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

**`/docs`**。就這個 URL，沒有 API 手冊 —— 手寫等於兩份真實來源，W2 一定分歧，
而分歧時會相信錯的那份。

16 個端點已凍結，與開工前準備 附錄 B 完全一致（`tests/test_contract.py` 會擋下
任何多出來或少掉的端點）。凍結後要改欄位必須先通知 P2。

現在就能做的事：

- 讀 `/docs` 產生 client、對著 schema 寫型別
- 每個端點未登入都回 401，可據此寫攔截器
- 錯誤碼照下表，不會再變

**還不能做的事**：端點的實作尚未在真實 PostgreSQL 上跑過（等 [D01]）。
契約是穩的，行為還沒驗過。

兩個會讓人意外的地方：

- **超長欄位不會回 422，會是資料庫錯誤。** `models.py` 刻意不重複實作長度檢查
  （守則 [P15]），長度規則的唯一來源是 `sql/001_schema.sql`
- **`password_hash` 永遠不會出現在任何回應裡**，有測試盯著

## 交給 P3 的東西

WS 協定在 開工前準備 附錄 A，程式化版本是 `app/realtime/protocol.py`。
P3 只需要三個函式（附錄 A.5）：

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

**還不能做的事**：使用者的真實暱稱與頭像要等 [D01] + [P12] 登入完成。
走廊門位 `/api/rooms` 的在線人數已經可用，但專案名稱要等資料庫。

## 錯誤碼慣例

> 開工前準備 附錄 C。

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

目錄結構刻意等於追蹤表的 Function 欄，任何一列任務都能直接對到一個檔案。

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

寫任何新程式之前先看守則 §4。摘要：

1. **座位認領**不得先查再寫 —— 送出 INSERT 並捕捉約束衝突（`SeatService.claim`）
2. **站內信查詢**的主體條件必須寫在 SQL 內，不得取全部再用 Python 過濾
3. **位置廣播**固定 10 Hz，且僅送本 tick 內有移動者；靜止時整則訊息不送

第 3 點有一個實際踩過的坑：只寫 `await asyncio.sleep(0.1)` 不夠。asyncio 判斷
計時器到期時會扣掉時鐘解析度（Windows 約 15.6 ms），事件迴圈忙碌時會提早醒來，
實測變成 94 ms 一次 ≈ 10.6 Hz。10 Hz 是硬上限不是目標值，所以 tick 迴圈以單調
時鐘對齊，提早醒來就補睡到真正到期。
