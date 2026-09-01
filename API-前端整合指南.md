# GuildHub API 前端整合指南

> 對象：P2（REST／看板）與 P3（即時層／場景）
> 後端版本：`app/main.py` FastAPI `title="GuildHub" version="0.1"`

## 這份文件的定位

**欄位層級的真實來源是 `/docs`（OpenAPI，自動產生），不是這份文件。**

```
sql/001_schema.sql  →  app/models.py  →  OpenAPI（/docs）  →  前端
```

這份文件補的是 `/docs` 產不出來的東西：認證流程、WebSocket 協定、錯誤碼的語意、
以及幾個「照 schema 寫會踩到」的實際行為。**欄位名稱與型別若與 `/docs` 不一致，
以 `/docs` 為準，並回報給後端。**

---

## 1. 快速開始

```bash
./run.sh
```

| 用途 | URL |
|---|---|
| REST base | `http://localhost:8000` |
| 互動式文件（權威） | `http://localhost:8000/docs` |
| OpenAPI JSON（產 client 用） | `http://localhost:8000/openapi.json` |
| WebSocket | `ws://localhost:8000/ws` |
| 健康檢查 | `GET /health` → `{"status":"ok"}` |

產 TypeScript client：

```bash
npx openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.d.ts
```

**大廳的 WebSocket 不需要登入、不需要資料庫**，`./run.sh` 之後可以直接連。
所有 REST 端點與房間場景則需要資料庫。

假資料：`psql "$DATABASE_URL" -f sql/002_seed.sql`（可重複執行）。
內含 28 張名片、22 則招募貼文、2 間已成軍的房間（密碼皆為 `guild1234`）。

---

## 2. CORS

**已設定好了**（`app/main.py` 的 `CORSMiddleware`）。預設放行兩個 dev origin：

```
http://localhost:5173     # Vite
http://localhost:3000     # CRA／Next
```

前端跑在這兩個 port 之一，直接打 `http://localhost:8000` 就會通，**唯一要記得的
是每個請求都要帶 `credentials`**（見 §3.1）——身分走 cookie，沒帶就是 401。

用別的 port 或別的網域，改後端的 `.env`：

```
CORS_ORIGINS=http://localhost:4321,https://guildhub.example.com
```

逗號分隔，**必須是完整 origin**（含 scheme 與 port），不能用 `*`——帶 cookie 的
跨源請求，瀏覽器規定 `Access-Control-Allow-Origin` 不得是萬用字元。填了 `*` 後端
會在啟動時就報錯，不會靜默地變成一個沒用的設定。

### 幾個相關的事實

- **前後端同源部署時把 `CORS_ORIGINS` 留空即可**——同源請求不經過 CORS
- **WebSocket 不受 CORS 管**（瀏覽器不對 WS 握手做 CORS 檢查），`ws://localhost:8000/ws`
  在任何 port 都連得上。cookie 一樣會自動帶
- **session cookie 是 `SameSite=Lax`。** localhost 的不同 port 算「同一個 site」
  （SameSite 只看網域，不看 port），所以本機開發沒問題。但**若正式環境把前後端
  放在不同網域**，Lax 會讓 cookie 送不出去——那時要改的是 cookie 的 `SameSite`
  與 `Secure`，不是 CORS。請先跟後端談
- 仍然可以改用 dev server proxy（Vite `server.proxy`）走同源，兩種都支援

---

## 3. 認證與 session

### 3.1 機制

身分放在 **session cookie**（Starlette `SessionMiddleware`，簽章 cookie，
非 JWT、無 Authorization header）。前端要做的只有一件事：

```js
fetch('http://localhost:8000/api/me', { credentials: 'include' })   // 跨源時必要
```

`credentials: 'include'` 是跨源時的必要條件，漏掉的症狀是**每個端點都回 401**，
而且看起來像後端壞了。用 axios 則是 `withCredentials: true`。

WebSocket 握手時瀏覽器會自動帶上 cookie，不需要（也無法）手動設定 header。

### 3.2 登入

規格 §9：匿名暱稱登入，**沒有密碼、沒有 OAuth**。

```http
POST /api/login
{"nickname": "晨風遊俠"}
→ 200 ProfileOut（同時 Set-Cookie）
```

**⚠️ 每次呼叫 `/api/login` 都會建立一張全新的名片。**
沒有「用同一個暱稱登回原本的身分」這條路徑 —— `login` 是 `insert`，不是查詢。
所以：

- 前端不要在每次開 app 時無條件呼叫 `/api/login`
- 正確流程是 **先 `GET /api/me`**，401 才顯示登入畫面
- 使用者清掉 cookie 等於舊名片變成孤兒，回不去了（發表日的預期行為）

### 3.3 未登入

所有 `/api/*` 端點（除了 `POST /api/login`）未登入一律 `401 {"detail":"未登入"}`。
可以據此寫一個攔截器：401 導向登入畫面。

`GET /api/me` 在「session 指向已不存在的名片」（例如資料庫重建過）時也回 401。

---

## 4. 錯誤格式與錯誤碼

FastAPI 標準格式：

```json
{"detail": "這個座位已經有人了"}
```

`detail` 是**中文、可直接顯示給使用者**的訊息（後端刻意這樣寫）。422 時 `detail`
會是 Pydantic 的錯誤陣列，不適合直接顯示。

| 碼 | 意義 | 前端該做什麼 |
|---|---|---|
| 400 | 業務規則擋下（座位編號超範圍、寄信給自己、房間未備妥） | 顯示 `detail` |
| 401 | 未登入 | 導向登入 |
| 403 | 非發起人／房間密碼錯誤／尚未通過房間驗證 | 顯示 `detail`，密碼錯則留在密碼框 |
| 404 | 專案／名片／收件人不存在 | 顯示「已不存在」，重新拉列表 |
| **409** | **座位衝突**（別人先坐了，或你已經有位子了） | **重新拉一次 `GET /seats` 並更新畫面** |
| 422 | 請求格式錯誤（型別不符、缺必填） | 這是前端的 bug，不該讓使用者看到 |
| 500 | 見 §8「已知的意外行為」 | — |

### 409 是正常流程，不是例外

座位認領由資料庫的唯一鍵衝突擋下，不是「先查再寫」。所以**兩個人同時點同一格，
其中一個一定會收到 409** —— 這是設計，不是 bug。兩種 409 用 `detail` 區分：

- `"這個座位已經有人了"` → 別人先坐了，刷新座位圖
- `"你已經在這個房間有座位了"` → 一人只能一格（沒有換位／退位端點）

---

## 5. REST 端點（16 個，已凍結）

端點集合由 `tests/test_contract.py` 鎖住，多一個少一個都會讓測試紅掉。
**凍結後要改欄位，後端會先通知前端。**

分頁一律 `?page=`，**每頁 20 筆，從 0 開始**。翻過尾頁回 `[]`（不是 404）。
回應沒有 total count，前端請以「回傳筆數 < 20」判斷已到底。

### 5.1 身分

| Method | Path | 說明 |
|---|---|---|
| POST | `/api/login` | body `{nickname}` → `ProfileOut`；**每次都建新名片**（見 §3.2） |
| GET | `/api/me` | → `ProfileOut`；未登入 401 |

### 5.2 人才看板

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/profiles?page=0` | → `ProfileOut[]`，依 `updated_at` 新到舊。**沒有搜尋與篩選**（規格明列不做） |
| GET | `/api/profiles/{profile_id}` | → `ProfileOut`；不存在 404 |
| PATCH | `/api/profiles/me` | body `ProfileUpdate`，**只送要改的欄位**，未給的不動 |

```jsonc
// ProfileOut
{
  "id": "uuid",
  "display_name": "晨風遊俠",
  "avatar_id": 0,                 // smallint，前端據此挑角色圖
  "skills": ["React", "TypeScript"],
  "hours_per_week": 18,           // 可為 null
  "bio": "前端。做過兩個小遊戲。",  // 可為 null
  "updated_at": "2026-08-31T10:00:00+00:00"
}
```

`ProfileUpdate` 的每個欄位都是選填：`display_name` / `avatar_id` / `skills` /
`hours_per_week` / `bio`。送 `{}` 是合法的（原樣回傳目前的名片）。

### 5.3 任務看板與專案

| Method | Path | 主體 | 說明 |
|---|---|---|---|
| POST | `/api/projects` | 登入 | body `ProjectCreate` → **201** `ProjectOut` |
| GET | `/api/projects?status=recruiting&page=0` | 登入 | → `ProjectOut[]`，**過期的不出現** |
| GET | `/api/projects/{project_id}` | 登入 | → `ProjectOut`；不存在 404 |
| POST | `/api/projects/{project_id}/form-team` | **發起人** | body `{password}` → `ProjectOut`（成軍） |
| POST | `/api/projects/{project_id}/close` | **發起人** | 無 body → `ProjectOut`（結案） |
| POST | `/api/projects/{project_id}/enter` | 登入 | body `{password}` → `{room_token}` |

```jsonc
// ProjectCreate（送出）
{
  "title": "找人一起做植物照顧提醒小工具",
  "body": "想做一個很小的東西……",
  "needed_skills": ["Python", "行動端"],   // 選填，預設 []
  "seat_count": 3                          // 選填，預設 4；實用範圍 1–8（見下）
}

// ProjectOut（回傳）
{
  "id": "uuid",
  "owner_id": "uuid",
  "title": "…",
  "body": "…",
  "needed_skills": ["Python"],
  "status": "recruiting",          // recruiting | active | closed
  "room_template": null,           // 成軍後才有值（0–3），前端據此挑房間背景圖
  "seat_count": 3,
  "expires_at": "2026-09-07T…",    // 建立時 +7 天，由資料庫填
  "updated_at": "2026-08-31T…"
}
```

**`password_hash` 永遠不會出現在任何回應裡**（有測試盯著）。

幾個行為要點：

- `status` 的預設查詢值是 `recruiting`。要查走廊上的房間用 `?status=active`
- **過期等於查不到，不是被刪除。** 沒有背景排程，過濾發生在查詢當下
- 「成軍」是一個動作同時完成三件事：`status` 轉 `active`、系統隨機指派
  `room_template`（0–3）、發起人設定房間密碼。**沒有「先開房再設密碼」的中間狀態**
- 「結案」會把 `status` 改成 `closed` 並**清空所有座位**，門從走廊消失
- owner-only 端點：非發起人 403、專案不存在 404
- `seat_count` 在資料庫沒有上限檢查，但 `seats.seat_index` 的 check 是
  `0 ≤ index < 8`。**所以 `seat_count > 8` 時第 9 格以後永遠坐不進去** ——
  前端請在建立表單就限制 1–8

### 5.4 房間門禁與座位

```
POST /api/projects/{id}/enter   →  拿到 room_token
```

`enter` 做兩件事：回傳 `room_token` 給你（WebSocket 要用），並把它存進你的
session（座位端點要用）。**前端不需要自己把 token 放進任何 header** ——
座位端點是從 session 讀的。

- 密碼錯 → `403 {"detail":"房間密碼錯誤"}`
- 專案不存在或**尚未成軍** → `404 {"detail":"專案不存在或房間尚未開啟"}`
- token 有效期 **8 小時**，過期後座位端點與 WS 握手都會失敗，要重新 `enter`

| Method | Path | 主體 | 說明 |
|---|---|---|---|
| GET | `/api/projects/{project_id}/seats` | room token | → `SeatOut[]`，依 `seat_index` 排序 |
| POST | `/api/projects/{project_id}/seats` | room token | body `SeatClaim` → **201** `SeatOut` |

```jsonc
// SeatClaim（送出）
{"seat_index": 2, "desk_template": 0}   // desk_template 選填，預設 0

// SeatOut（回傳）
{"seat_index": 2, "user_id": "uuid", "desk_template": 1,
 "claimed_at": "2026-08-31T…"}
```

- 沒有 `enter` 過就打座位端點 → `403 {"detail":"尚未通過房間密碼驗證"}`
- `seat_index` 範圍是 `0 ≤ index < seat_count`；超過會回 400，`detail` 會直接
  寫明「這個房間只有 N 個座位（可選 0–N-1）」
- **沒有釋放座位的端點**（已於 WBS v0.2 砍除）。座位只在結案時全數清空
- 座位滿等於房間滿，不需要另外判斷容量

### 5.5 站內信

| Method | Path | 說明 |
|---|---|---|
| POST | `/api/messages` | body `{recipient_id, body}` → **201** `MessageOut` |
| GET | `/api/messages?page=0` | → `MessageOut[]`，依 `created_at` 新到舊 |

```jsonc
// MessageOut
{"id":"uuid","sender_id":"uuid","recipient_id":"uuid",
 "body":"…","created_at":"…","read_at":null}
```

- **收件匣同時包含寄出與收到的信**（`sender_id = 我 OR recipient_id = 我`）。
  前端要自己比對 `sender_id === me.id` 來分收發
- **站內信不可編輯、不可刪除**（immutable，規格 §4.2）。`PATCH` / `DELETE`
  端點不存在且不會有
- **`read_at` 永遠是 `null`**：沒有任何端點會標記已讀。若要做未讀紅點，
  目前只能在前端本地記錄，或請後端加端點（等於改契約）
- 寄給自己 → `400 {"detail":"不能寄信給自己"}`
- 收件人不存在 → `404 {"detail":"收件人不存在"}`

### 5.6 走廊門位 ★

```http
GET /api/rooms
→ [{"project_id":"uuid","title":"晨光工作室","online_count":3}, …]
```

已成軍（`active`）的專案就是走廊上的門。**最多回 12 個**，依 `updated_at` 新到舊。
`closed` 的專案門會消失。

`online_count` 來自即時層的記憶體，**不是資料庫**，每次呼叫都是當下的真實人數。
它不會透過 WebSocket 推播，前端若要更新走廊人數，需要自行輪詢（建議 5–10 秒一次）。

門的排列是一維的：`x = index × 間距`，**照回傳順序擺即可**，沒有版面演算法。

---

## 6. WebSocket 協定 v1

> 這是全案唯一手寫的介面文件（REST 有 OpenAPI，WS 沒有）。
> 程式化版本：`app/realtime/protocol.py` —— 那個檔案改了，這一節就過期了。

### 6.1 連線

```
大廳： ws://host/ws?scene=lobby
房間： ws://host/ws?scene=room:{project_id}&token={room_token}
```

- 身分來自 session cookie（瀏覽器自動帶）
- **一條連線只屬於一個 scene。切換場景等於關掉重開**，沒有 switch 訊息
- 房間必須帶 `token`（`/enter` 拿到的那個），握手時就驗；失敗直接關閉連線
  （close code `1008`，**不會有 `err` 訊息** —— 連線根本沒 accept）
- 大廳不驗 token，**且可以匿名連**（沒登入的人暱稱一律「訪客」）
- **房間不能匿名進**：握手會比對 token 的簽發對象與連線者，匿名連線拿到的是
  隨機身分，對不上任何一張票

握手失敗的三個原因（前端只會看到連線被關）：scene 格式不合法、token 無效或過期、
token 不屬於這個房間或這個人。

### 6.2 client → server

| 訊息 | 格式 | 頻率 |
|---|---|---|
| 移動 | `{"t":"move","x":120,"y":340,"f":2}` | **前端自行節流至 10 Hz，且僅在座標變動時送** |
| 狀態文字 | `{"t":"status","text":"趕工中"}` | 變更時 |
| 對話 | `{"t":"chat","body":"…"}` | 送出時 |

- `f` 是 facing：`0=下 1=左 2=右 3=上`
- **`x` / `y` 必須是整數**。送浮點會被協定擋下（`StrictInt`），整則訊息被丟棄
- **不合法的訊息一律靜默丟棄，不會回錯誤訊息。** 送出去沒反應等於格式不對，
  開發期請用瀏覽器的 WS 面板自己核對
- **狀態文字上限 12 個字**（字數，不是位元組，中文算 1 字）。超過會被**丟棄**，
  舊狀態維持不變 —— 前端請自己在輸入框限制 `maxlength=12`，否則使用者會以為壞了

### 6.3 server → client

| 訊息 | 格式 | 時機 |
|---|---|---|
| 握手完成 | `{"t":"hello","you":"<uuid>","hz":10}` | 連線建立時一次 |
| 全場快照 | `{"t":"snapshot","players":[{"id","name","av","x","y","f","st"}]}` | 進場時一次 |
| 位置差量 | `{"t":"pos","p":[["<id>",120,340,2],…]}` | 每 100 ms，**僅含本 tick 有移動者** |
| 進出場 | `{"t":"presence","join":[{…同 snapshot 元素…}],"leave":["<id>"]}` | 有人進出時 |
| 狀態文字 | `{"t":"status","id":"<id>","text":"開會中"}` | 有人變更時 |
| 對話 | `{"t":"chat","id":"<id>","name":"…","body":"…"}` | 有人發言時 |
| 錯誤 | `{"t":"err","code":"…","msg":"…"}` | 保留（目前未使用） |

訊息順序保證：`hello` → `snapshot` → 之後才是差量。`snapshot` 已包含自己。

`snapshot` / `presence.join` 的元素欄位：`id`、`name`、`av`（avatar_id）、
`x`、`y`、`f`、`st`（狀態文字，登入時為空字串）。

### 6.4 四件會讓人以為壞掉的事

1. **靜止時完全收不到 `pos`。** 沒有人移動時整則訊息不送（不是送空陣列）。
   收不到 `pos` **不代表斷線**，不要拿它當心跳
2. **位置差量是陣列不是物件**：`["<id>",120,340,2]` 對應 `[id, x, y, facing]`。
   40 人 × 10 Hz 下欄位名會被重複送 400 次／秒，所以省掉了
3. **自己的 `move` 會原路廣播回自己。** 後端不做逐人過濾。前端應以本地預測為準，
   收到自己的 id 時直接忽略（或用來做校正）
4. **對話不落地。** 沒有歷史訊息 API、重整後聊天室是空的 —— 這是規格 §3.4 的設計，
   不是還沒做

### 6.5 前端只需要三個函式

```javascript
onSnapshot(players)      // 進場全量，建立所有角色
onPositions(list)        // 每 100 ms 的差量，餵給 interpolate()
sendMove(x, y, facing)   // 自行節流至 10 Hz
```

位置更新是 10 Hz（100 ms），畫面是 60 fps —— **中間必須自己做插值補間**，
直接把 `pos` 貼到座標上會看到明顯的跳動。

### 6.6 開發用工具

讓大廳有 40 個會走動的假人，拿來調插值：

```bash
python tools/run_swarm.py --n 40 --seconds 300
```

驗證靜止時封包數為 0：

```bash
python tools/run_swarm.py --n 5 --idle
```

---

## 7. 端到端流程

```
1. GET  /api/me                    → 401 就顯示登入畫面
2. POST /api/login {nickname}      → 拿到 ProfileOut，cookie 已設好
3. WS   /ws?scene=lobby            → 進大廳，走動、聊天、狀態文字
4. GET  /api/profiles?page=0       → 人才看板
   GET  /api/projects?page=0       → 任務看板（招募中）
5. POST /api/projects              → 貼出自己的招募
6. POST /api/messages              → 私訊談隊
7. POST /api/projects/{id}/form-team {password}
                                   → 成軍，門出現在走廊
8. GET  /api/rooms                 → 走廊門位（含在線人數）
9. POST /api/projects/{id}/enter {password}
                                   → 拿 room_token
10. WS  /ws?scene=room:{id}&token={room_token}
    GET /api/projects/{id}/seats   → 座位圖
    POST /api/projects/{id}/seats {seat_index}
                                   → 認領座位（409 要能處理）
11. POST /api/projects/{id}/close  → 結案，座位清空、門消失
```

第 3 步的大廳**不需要 1、2 步也能連**（匿名），前端可以先做這條路徑，
不必等資料庫。

---

## 8. 已知的意外行為

這幾條是後端刻意的設計，不是待修的 bug —— 踩到不用回報，照著處理即可。

| 現象 | 原因 | 前端該做什麼 |
|---|---|---|
| **超長欄位回 500，不是 422** | 長度規則只寫在資料庫（`sql/001_schema.sql`），應用層刻意不重複檢查 | **前端自己擋長度**（見下表），不要依賴後端回 422 |
| 重複呼叫 `/api/login` 會一直長出新名片 | `login` 是 insert | 先 `GET /api/me` |
| `read_at` 永遠是 null | 沒有標記已讀的端點 | 未讀狀態目前只能做在前端 |
| 收不到 `pos` | 靜止時不送 | 不要當斷線 |
| WS 送了訊息但沒反應 | 不合協定的訊息靜默丟棄 | 檢查格式（尤其 `x`/`y` 必須是整數） |
| 走廊人數不會自己更新 | `online_count` 只在 REST 回應裡 | 自行輪詢 |
| 跨源時每個端點都回 401 | 沒帶 `credentials`，cookie 沒送出去 | `credentials: 'include'`（見 §3.1） |

### 前端該擋的長度上限（來源：`sql/001_schema.sql`）

| 欄位 | 限制 |
|---|---|
| `profiles.display_name` | 1–20 字 |
| `profiles.bio` | 300 字以內 |
| `messages.body` | 1–2000 字 |
| WS 狀態文字 | 12 字以內（超過會被靜默丟棄） |
| `projects.title` / `body` | 無資料庫上限，但仍建議前端設合理值 |
| `seats.seat_index` | 0–7，且必須小於該專案的 `seat_count` |

---

## 9. 確定不存在的端點

以下列出來是為了避免前端等一個不會來的東西：

- `PATCH` / `DELETE` `/api/messages/{id}` —— 站內信 immutable（規格 §4.2）
- `DELETE /api/projects/{id}/seats/{index}` —— 釋放座位已砍除
- `POST /api/projects/{id}/reset-password` —— 密碼重設已砍除
- 任何 `/api/admin/*`
- 搜尋、篩選、標記已讀、聊天歷史、通知、好友、續期／到期提醒

需要其中任何一個，都是**改契約**，要先跟後端談。

---

## 10. 有疑問時看哪裡

| 問題 | 看哪 |
|---|---|
| 某個欄位的型別／是否選填 | `/docs`（權威） |
| WS 訊息的確切格式 | `app/realtime/protocol.py` |
| 某個 400／409 的觸發條件 | `app/api/*.py` 的 docstring，寫得很清楚 |
| 長度與唯一性限制 | `sql/001_schema.sql` |
| 端點清單是否變動 | `tests/test_contract.py` 的 `EXPECTED` |
