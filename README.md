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
