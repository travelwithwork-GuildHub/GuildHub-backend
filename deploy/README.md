# 部署

> 2026-09-08 裁決：**正式部署走 Railway**，本目錄的 `guildhub.service` 與
> `deploy.sh` 保留為 VM 備援方案（見 §8），不刪除。
>
> 理由：後端剩下的工作有一半是部署，而 P1 同時要做備援錄影與簡報。
> Railway 省下裝 PostgreSQL、裝 Caddy、申請網域、設 systemd 與防火牆
> ——約三小時換成半小時。距發表日只剩兩週，這個交換划算。
>
> **2026-09-14 補充：不花任何額外費用，而且前後端必須同源。**
> 前後端各拿一個免費子網域會是跨站，Safari 會把 session cookie 當第三方
> cookie 擋掉（理由見 §0 第四列）。不買網域的唯一解法是**只對外開一個網址**，
> 由 Caddy 依路徑分流給前端與後端。後端程式不用改，只改設定。
>
> **2026-09-15：已照本文件實際部署完成**，[D03] 驗收通過（紀錄見 §5）。
> 本文件依實際踩到的狀況改寫過，§2 是**真的走過一次**的步驟，不是推測。

**全案唯一的對外網址：`https://gateway-production-3ecd.up.railway.app`**

| 用途 | 網址 |
|---|---|
| 前端 | `https://gateway-production-3ecd.up.railway.app/world` |
| Swagger（給前端查 API） | `https://gateway-production-3ecd.up.railway.app/docs` |
| REST base | `https://gateway-production-3ecd.up.railway.app` |
| WebSocket | `wss://gateway-production-3ecd.up.railway.app/ws` |

---

## 0. 這個服務的四個硬條件

部署到哪裡都一樣，違反任何一條都會壞，而且**多半沒有錯誤訊息**：

| 條件 | 為什麼 | 違反的症狀 |
|---|---|---|
| **只能有一個 process** | 即時層的狀態全在記憶體（`SceneRegistry`／`PresenceStore`／`Broadcaster` 是 module-level 物件） | 大廳裡兩個人看不到彼此，隨機發生 |
| **不能睡** | 冷啟動等於台上等一分鐘 | 發表日第一個請求逾時 |
| **必須 HTTPS** | HTTPS 頁面不能連 `ws://`（mixed content） | 頁面打得開，WebSocket 連不上 |
| **前後端必須同源** | `vercel.app`、`up.railway.app`、`onrender.com`、`trycloudflare.com`、`duckdns.org` **全部在 [Public Suffix List](https://publicsuffix.org/list/public_suffix_list.dat) 上**（2026-09-14 查過）—— 瀏覽器把 `a.vercel.app` 與 `b.up.railway.app` 當成兩個不相干的網站。跨站的 session cookie 是第三方 cookie，**Safari（含 iPhone 上所有瀏覽器）預設擋，Chrome 無痕模式也擋**。`COOKIE_CROSS_SITE=true` 只救得了桌機 Chrome 一般模式 | 登入回 200，然後每個 API 都 401；WebSocket 靜默變成匿名的「訪客」，進房間被拒 |

第四條不是設定修得好的問題，是**網址的形狀**。所以整份文件圍繞它排。

---

## 1. 形狀：一個網址，依路徑分流

```
                  https://gateway-production-3ecd.up.railway.app     ← 唯一對外公開的網址
                                       │
                            gateway（Caddy，Railway）
                    ┌──────────────────┴──────────────────┐
   /api/*  /ws  /health  /docs  /openapi.json          其他所有路徑
                    │                                     │
     backend（FastAPI，Railway 內網，不對外）     https://guildhub-frontend.vercel.app
                    │
          Postgres（Railway 內網，不對外）
```

- 瀏覽器只看得到閘道那一個網址 → 同源 → cookie 是第一方的，`SameSite=Lax` 就夠
- **同源請求不經過 CORS**，所以後端的 `CORS_ORIGINS` 留空、`COOKIE_CROSS_SITE` 保持 `false`
- WebSocket 握手也是同源，cookie 自然帶上，身分與 room token 持有人比對（[R31]）都正常

⚠️ **前端自己的 `/api/*` Route Handlers（`NEXT_PUBLIC_DATA_ADAPTER=internal` 用的那組）
會被閘道蓋掉。** 在閘道網址底下，所有 `/api` 都是真後端。

⚠️ **FastAPI 的 `/redoc` 沒有分流**，會落到前端回 404。前端要查 API 用 `/docs`（Swagger）。

---

## 2. Railway 步驟（2026-09-15 實際走過）

### 2.1 帳號與部署方式

1. 用 GitHub 帳號註冊 Railway，**完成 GitHub 驗證**。沒驗證的帳號是 Limited Trial，
   對外網路與可用 port 受限
2. Trial 是一次性 **$5 額度、30 天**（先到者為準），之後降為 Free plan：
   每月 $1、不累積 —— **撐不住常駐服務**。發表後要長期跑就換 §8
3. **部署走 Railway CLI，不走 GitHub 連動。** 這個 repo 在組織
   `travelwithwork-GuildHub` 底下，Railway 的 GitHub App 要組織 admin 才能安裝
   （實際錯誤：`You do not have permissions to install the Railway GitHub app`）。
   CLI 從本機上傳，不需要任何人核准

   手動部署反而是這個服務要的：**push 不會觸發部署**，部署時機完全在人手上，
   天然守住 §4 第 6 條「發表當天不部署」

   ```bash
   npm i -g @railway/cli
   railway login
   ```

> 以下服務名稱**大小寫要完全一致**：`Postgres`、`backend`、`gateway`。
> 變數參照 `${{服務名.變數}}` 靠名稱對應，打錯不會報錯，只會是空值。

### 2.2 Postgres

1. New Project → **Deploy PostgreSQL**，服務名稱保持 `Postgres`
2. Settings → Regions → **Southeast Asia (Singapore)**（對台灣約 60–70ms）。
   趁還沒資料時改，有資料之後換區域會觸發搬移

實際建出來的是 **PostgreSQL 18**（`ghcr.io/railwayapp-templates/postgres-ssl:18`），
本機開發是 16；`001_schema.sql` 在 18 上套用成功。

**這個資料庫沒有公開網址**：Variables 裡只有內網的 `DATABASE_URL`，**沒有
`DATABASE_PUBLIC_URL`**。這是對的 —— 不要為了灌資料去開 TCP Proxy，灌資料走
§2.5 從容器裡做。

### 2.3 backend 服務

**+ Create → Empty Service**，然後逐項設定：

| 位置 | 設定 |
|---|---|
| Settings 最上方 | 服務名稱 **`backend`** |
| Settings → Deploy → Regions | Singapore，Replicas **1** |
| Settings → Deploy → Custom Start Command | 見下方 |
| Settings → Deploy → Healthcheck Path | **留空**（理由見 §4 第 5 條） |
| Settings → Deploy → Serverless | **關閉** |
| Settings → Deploy → Restart Policy | On Failure（免費方案上限 10 次，預設即可） |
| Settings → Networking | **不要 Generate Domain** —— 後端不對外 |
| Variables → Raw Editor | §3 那張表 |

Start Command：

```
python -m uvicorn app.main:app --host :: --port $PORT --proxy-headers --forwarded-allow-ips '*' --ws-ping-interval 20 --ws-ping-timeout 20
```

Railpack 建置時 Start Command 是**經過 shell 執行**的（Railway 官方文件），所以
`$PORT` 會展開、`'*'` 的引號會被正確解析。

空服務按 Apply changes 時可能顯示部署失敗 —— 還沒有程式碼，正常。

⚠️ 空服務的 **Networking 面板可能顯示 `Could not load public networking`**。
部署之前就是這樣，不影響任何事，後端本來也不開公開網址。

### 2.4 部署 backend

**先確認本機跟 GitHub 的 main 一模一樣。** CLI 上傳的是本機資料夾，不是 GitHub
上的 commit —— 有沒 commit 的改動，就會把它部署上去：

```bash
git fetch && git status -sb      # 必須只有一行 "## main...origin/main"
```

```bash
railway link --project <專案 ID> --environment production --service backend
railway up --service backend --detach
```

- `railway link` 的連結資訊存在 CLI 的全域設定，**不會在 repo 裡產生檔案**
- 上傳時依 `.gitignore` 排除 `.env` 與 `.venv`（實測：容器裡沒有 `.env`，密鑰只在 Railway 的變數裡）
- 建置用的是 Python 3.11（`RAILPACK_PYTHON_VERSION`），相依套件是 `cp311` wheel

✅ Deploy Logs 出現 `Uvicorn running on http://[::]:8000`，而且**沒有**
`資料庫未連上，僅即時層可用` 的警告。

### 2.5 灌 schema 與假資料（從容器裡）

資料庫沒有公開網址，所以進 backend 容器，用容器裡的內網 `DATABASE_URL` 灌：

```bash
railway ssh --service backend -- python tools/apply_sql.py --seed
```

第一次用 `railway ssh` 要先做兩件事：

1. **登記 SSH 公鑰**（只上傳 `.pub`）：

   ```bash
   railway ssh keys add --key 'C:\Users\<你>\.ssh\id_ed25519.pub' --name <名稱>
   ```

   Windows 上 `~/.ssh/...` 這種寫法會回 `Key not found`，要用 Windows 路徑

2. **在自己的終端機互動連一次**，確認主機指紋後輸入 `yes`：

   ```bash
   railway ssh --service backend
   ```

   沒做這一步的話，非互動執行會直接 `Host key verification failed`。
   2026-09-15 看到的 `ssh.railway.com` 指紋是
   `SHA256:+S1xg92FrnHz6pY3bpkmh1OGtWQGNANXilPzlxA7B1g`（ED25519）。
   Railway 文件**沒有公布**官方指紋，只能首次信任；之後指紋變了就要停下來查

✅ 腳本最後會列出筆數：`profiles 28`、`projects 24`（22 則招募中 + 2 間已成軍的房間）、
`seats 4`、`messages 4`。

⚠️ **不要加 `--reset`**，那會先刪光資料。

### 2.6 gateway 服務

閘道的檔案**不在這個 repo**（這裡禁止 Dockerfile），放在 `guildhub-gateway/`，
與本 repo 同一層：`Caddyfile`、`Dockerfile`、`README.md` 三個檔。

**+ Create → Empty Service**：

| 位置 | 設定 |
|---|---|
| Settings 最上方 | 服務名稱 **`gateway`** |
| Settings → Deploy → Regions | Singapore，Replicas 1 |
| Settings → Deploy → Serverless | **關閉** |
| Settings → Deploy → Custom Start Command | **留空**（啟動指令在 Dockerfile 裡） |
| Variables | 見下表 |

| 變數 | 值 | 備註 |
|---|---|---|
| `BACKEND_DOMAIN` | `${{backend.RAILWAY_PRIVATE_DOMAIN}}` | 解析後是 `backend.railway.internal` |
| `BACKEND_PORT` | `${{backend.PORT}}` | 解析後是 `8000`，所以 backend 一定要設 `PORT` |
| `FRONTEND_URL` | `https://guildhub-frontend.vercel.app` | **只要網站根網址**，不要帶 `/world`；要用 production 網址，不是 preview |

`PORT` 不要設，Railway 自己給（實測是 `8080`）。`FRONTEND_URL` 沒設時，
後端以外的路徑回 502，後端路徑照常能驗收。

部署與開網址：

```bash
cd ../guildhub-gateway
railway link --project <專案 ID> --environment production --service gateway
railway up --service gateway --detach
railway domain --service gateway --port 8080
```

✅ Deploy Logs 出現 `adapted config to JSON` 與 `server running`，監聽 `:8080`。
`HTTP/2 skipped`、`HTTP/3 skipped because it requires TLS` 兩則 WARN 是正常的 ——
TLS 在 Railway 邊緣終止，閘道本身只跑 HTTP/1.1。

**改 `FRONTEND_URL` 之類的變數會自動觸發 gateway 重新部署**（`railway variables --set`
預設如此），不用再 `railway up`。

`Caddyfile` 跟 Railway 官方模板
[`caddy-reverse-proxy`](https://github.com/railwayapp-templates/caddy-reverse-proxy)
不一樣的三個地方，**改之前先讀懂**：

| 模板 | 我們 | 為什麼 |
|---|---|---|
| `handle_path /api/*` | `handle @backend` | `handle_path` 會把 `/api` 剝掉再轉。我們的路由本來就以 `/api` 開頭，剝掉就全部 404 |
| `dynamic a { versions ipv4 ipv6 }` | `versions ipv6` | 後端 `uvicorn --host ::` 只收 IPv6（見 §4 第 5 條），給 IPv4 位址只會連線失敗再重試 |
| 前端也在 Railway 內網 | `reverse_proxy {$FRONTEND_URL}` 加 `header_up Host {upstream_hostport}` | 前端在 Vercel。Vercel 依 Host 決定是哪個專案，不改 Host 回 404 |

### 2.7 前端（Vercel）

前端的兩個位址**都填閘道網址**，改完要在 Vercel 重新部署才會生效
（`NEXT_PUBLIC_*` 是建置時編進 JS 的）：

```
NEXT_PUBLIC_APP_ENV=production
NEXT_PUBLIC_DATA_ADAPTER=guildhub
NEXT_PUBLIC_GUILDHUB_REST=https://gateway-production-3ecd.up.railway.app
NEXT_PUBLIC_REALTIME_ADAPTER=guildhub
NEXT_PUBLIC_GUILDHUB_WS=wss://gateway-production-3ecd.up.railway.app/ws
```

- **使用者只能從閘道網址進站。** 直接開 `guildhub-frontend.vercel.app` 就回到跨站，
  Safari 登入會壞。簡報、QR code、群組裡貼的網址一律是閘道網址
- Vercel 每個 PR 的 preview 子網域**不接正式後端**，用 `NEXT_PUBLIC_REALTIME_ADAPTER=none`
  或接本機後端
- 前端的建置閘門**不檢查 `NEXT_PUBLIC_GUILDHUB_REST`**（前端 `docs/DEPLOY.md`），
  漏設要到頁面載入才拋錯
- 確認設定真的編進去了：抓 `/world` 引用的 `/_next/static/**/*.js`，搜尋
  `gateway-production-3ecd`。搜不到、而且看到 `cN("none")` 的話，那還是單人預覽版

---

## 3. 後端環境變數

| 變數 | 值 | 備註 |
|---|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | 用變數參照，不要手抄。解析後是 `*.railway.internal` |
| `SESSION_SECRET` | 隨機長字串 | 換掉會讓所有人登出 |
| `ROOM_TOKEN_SECRET` | 隨機長字串 | 同上 |
| `CORS_ORIGINS` | **空字串** | 同源不經過 CORS。**要設成空值，不要不設** —— 不設會套用預設的 localhost 清單（無害，但不是這個部署的意圖） |
| `COOKIE_CROSS_SITE` | `false`（或不設） | 同源不需要 `SameSite=None` |
| `PORT` | `8000` | 給 gateway 的 `BACKEND_PORT` 參照用 |
| `RAILPACK_PYTHON_VERSION` | `3.11` | Railpack 預設 3.13.2；`requirements.txt` 是在 3.11 上釘死的 |

產生密鑰：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 什麼時候才用 `COOKIE_CROSS_SITE=true`

只有前後端**確定分屬不同網域**、而且接受 Safari 登入會壞的時候。這個專案在零費用
的前提下**不走這條**。開關與它的啟動檢查（CORS 有非本機來源卻沒開就炸掉）保留著，
是給日後真的有自訂網域時用的。

---

## 4. 八個會咬人的地方

**1. Start Command 絕對不能有 `--workers`。**
理由見 §0 第一列。Railway 不會攔你，uvicorn 也不會報錯。

**2. Replicas 保持 1。**
Railway 的水平擴容按一下就開，按下去就是多一個互相看不見的世界。
**這件事要寫進交接說明**，否則日後有人「順手優化」。

**3. `$PORT` 是平台指定的**，Start Command 裡不能寫死。`run.sh` 已經改成
`${PORT:-8000}`，本機行為不變。

**4. `--proxy-headers` 必加，而且要 `--forwarded-allow-ips '*'`。**
uvicorn 預設只信任 `127.0.0.1` 送來的 `X-Forwarded-*`，而閘道在內網的來源 IP
不固定。沒有它，FastAPI 會以為自己是 http。

**5. 後端綁 `::`，而且那代表「只收 IPv6」。**
uvicorn 單一 process 模式走 asyncio 的 `create_server`，綁 `::` 時會設
`IPV6_V6ONLY`，**不是 dual-stack**（Railway 官方 Caddy 模板也這樣註明）。後果有三：

- gateway 從內網連得到（Railway 內網一定有 IPv6），這是我們要的
- 後端開不了公開網址 —— 本來就不開
- **Healthcheck Path 要留空**：健康檢查不保證走 IPv6，設了可能一直判定失敗、部署卡住

2025-10-16 之後建立的 Railway 環境內網也支援 IPv4，但沒有理由改回 `0.0.0.0`：
那會讓後端與閘道的設定各自依賴不同的假設。

**6. 每次 deploy = 所有連線斷開。**
記憶體狀態歸零，大廳裡的人全部被踢出。**發表當天不要部署**
（前端 push 到 Vercel 不會斷後端連線，但會換掉頁面，一樣不要）。
改 gateway 的變數也會觸發 gateway 重新部署，一樣算。

**7. Railway 的日誌上限是每秒 500 行，超過就丟。**
2026-09-15 的 40 併發測試就撞到過（`Messages dropped: 183`），起因是一個會在大量
同時斷線時噴 traceback 的 bug（已修，[R24]）。發表結束時觀眾同時關頁面是同一個
情境 —— **任何會在斷線時逐條記錄的東西都要小心**，那時被擠掉的可能是真正要看的那幾行。

**8. Swagger 的 Try it out 打的是正式資料庫。**
`/docs` 開給前端查 API 很方便，同源所以在頁面上登入之後，後面的呼叫都帶著 cookie。
但**每一次 `POST /api/login` 帶 `nickname` 都會建一張新名片**，建專案、寄信也都是
真的寫進去，人才看板上會多出測試資料。要試登入流程，用 seed 名片的
`resume_token`（例如 `11111111-0000-4000-8000-000000000001`），不會新增資料。

---

## 5. 驗收（[D03]）

以下 `<網址>` 一律是**閘道網址**。

### 5.1 HTTP 路由

```bash
curl -i https://<網址>/health           # 200，{"status":"ok"}
curl -I https://<網址>/docs             # 200，Swagger
curl -i https://<網址>/api/me           # 401 {"detail":"未登入"} —— 證明 /api 沒被剝掉
curl -I https://<網址>/world            # 200，前端（FRONTEND_URL 沒設時是 502）
```

### 5.2 session 與即時層

用 seed 名片的 `resume_token` 登入（**不會新增名片**），拿 cookie 走完一輪：
`POST /api/login` → `GET /api/me` → 兩個看板 → `POST /api/projects/{id}/enter`
（seed 房間密碼 `guild1234`）→ 帶 cookie 連 `wss://<網址>/ws` → 帶 room token
連房間場景 → 不帶 token 連房間要被拒。

### 5.3 併發（[D05] 用同一條，`--n 50`）

```bash
python tools/run_swarm.py --url wss://<網址>/ws --n 40 --seconds 60
python tools/run_swarm.py --url wss://<網址>/ws --n 5 --idle      # 靜止時 pos 必須是 0
```

> ⚠️ **這會影響正在用這個環境的人**：40 個 bot 會出現在大廳裡。跑之前在群組說一聲，
> 不要在別人串接測試時跑。跑完看一次 backend 的日誌有沒有 ERROR。

### 5.4 瀏覽器驗收（§0 第四列的證明，不能省）

| 裝置 | 步驟 | 預期 |
|---|---|---|
| 桌機 Chrome | 開閘道網址 → 登入 → 重新整理 | 仍是同一張名片（`GET /api/me` 200） |
| **Safari（Mac 或 iPhone）** | 同上 | 同上。**這一條過了才算同源真的成立** |
| Chrome 無痕 | 同上 | 同上 |
| 兩個裝置 | 同時進大廳走動 | 互相看得見，名字不是「訪客」 |
| 任一裝置 | 建專案 → 成軍 → 用密碼進房間 | WebSocket 連 `room:{id}` 成功（沒被 1008 關掉） |

**重啟驗證**：對 backend 重新部署一次（`railway up` 或網頁上 Redeploy），
等服務起來再跑一次 5.1–5.3。

### 5.5 紀錄

| 日期 | 項目 | 結果 |
|---|---|---|
| 2026-09-15 | 5.1 HTTP 路由 | ✅ 全部符合預期（`/` 在設 `FRONTEND_URL` 前為 502，設了之後 200，JS 由 Vercel `sin1` 節點回應） |
| 2026-09-15 | 5.2 session 與即時層 | ✅ cookie 為 `httponly; samesite=lax`；`hello.you` 是本人；`move` 經閘道廣播回來；不帶 token 進房 403 |
| 2026-09-15 | 5.3 40 併發 60 秒 | ✅ 0 失敗，每連線 pos 8.4 Hz（上限 10）；靜止 pos = 0。**但抓到斷線時噴 traceback 的 bug**，已修（[R24]） |
| 2026-09-15 | 5.4 瀏覽器 | ⏳ 等前端改成接真後端的建置 |
| 2026-09-16 | 重啟驗證（backend 重新部署為含 [R24] 修正的版本後） | ✅ 5.1、5.2 全部通過；資料保留（名片仍 28 張）|
| 2026-09-16 | 5.3 改跑 5 連線 10 秒 | ✅ 0 失敗，每連線 pos 9.2 Hz；**斷線後日誌 0 行 ERROR**。當時大廳有一位訪客在線，所以沒跑 40 條 —— 40 條同時斷線的情境由 `tests/realtime/test_disconnect_after_send_failure.py` 證明，正式環境的 40 條複跑併入 [D05] |

---

## 6. 費用

2026-09-14 查的 Railway 官方價格：RAM $10／GB／月、vCPU $20／月、Volume $0.15／GB／月、
對外流量 $0.05／GB。

三個服務（Postgres、後端、Caddy）閒置時合計約 250 MB RAM，**粗估一天 $0.1 上下**，
開到 9/23 用不到 Trial 額度的一半。這是估算，**開通後在 Usage 頁面看實際數字**，
發表前一週再看一次。

前端在 Vercel 免費方案，不佔 Railway 額度。

---

## 7. 備援 B：本機 + Cloudflare Quick Tunnel

Railway 出狀況時的第二道防線（第三道是備援錄影）。**不需要任何帳號**。

同一台電腦跑三樣東西，再用 Quick Tunnel 拿一個 HTTPS 網址：

```
後端      bash run.sh                             → 127.0.0.1:8000
前端      next start（在前端 repo）               → 127.0.0.1:3101
閘道      caddy run --config Caddyfile.local      → 127.0.0.1:8080
通道      cloudflared tunnel --url http://127.0.0.1:8080
```

`Caddyfile.local`（放在這台電腦上，不進任何 repo）：

```
{
	admin off
	auto_https off
}

:8080 {
	@backend path /api/* /ws /health /docs /openapi.json
	handle @backend {
		reverse_proxy 127.0.0.1:8000 {
			header_up X-Forwarded-Proto https
		}
	}

	handle {
		reverse_proxy 127.0.0.1:3101
	}
}
```

限制（Cloudflare 官方文件）：

- **只供測試與開發，不保證任何 SLA**
- 同時最多 200 個進行中的請求，超過回 429（40 人夠用）
- 文件沒有提 WebSocket，**演練時實測**
- **每次重開 `cloudflared` 網址就會變**，前端的 `NEXT_PUBLIC_GUILDHUB_*` 是建置時
  寫死的，網址變了就要重新 `next build`。演練時把「換網址 → 重建前端」的時間量出來
- 這台電腦與它的網路就是整個服務，發表當天不能睡眠、不能換網路

後端的 `.env` 在這個情境一樣是 `CORS_ORIGINS=` 空值、`COOKIE_CROSS_SITE=false`。

---

## 8. 備援 C：VM + systemd（發表後長期跑）

本目錄的 `guildhub.service` 與 `deploy.sh` 仍然可用，兩種情況會用到：

- Railway 額度用完或出狀況，而且有時間換
- 發表後要長期跑：Railway Trial 結束後的 Free plan 每月只有 $1，撐不住常駐服務

選項是 Oracle Cloud Always Free（東京）。**永久免費，但註冊要信用卡驗證**（不扣款），
而且註冊可能被拒、ARM 機型常常沒有容量 —— 所以**不要把它排進發表前的關鍵路徑**。

```bash
sudo cp deploy/guildhub.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now guildhub
```

VM 路線額外要做的：裝 PostgreSQL、裝 Caddy（自動 HTTPS）、申請一個 DuckDNS 免費
子網域。前後端**一樣要同源**，Caddy 設定：

```
guildhub.duckdns.org {
	@backend path /api/* /ws /health /docs /openapi.json
	handle @backend {
		reverse_proxy 127.0.0.1:8000
	}

	handle {
		reverse_proxy 127.0.0.1:3101
	}
}
```

`guildhub.service` 綁的是 `0.0.0.0:8000`，Caddy 在同一台機器用 `127.0.0.1` 連，
IPv4 就夠，不需要 `::`。**防火牆只開 80／443，不要開 8000** —— 8000 對外開著的話，
繞過 Caddy 直連後端就又是另一個 origin。

不要用 nginx —— 要自己處理 certbot 與 WebSocket 的 `Upgrade` header，
多半小時而且容易漏掉 WS 那段。
