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
                          https://<閘道>.up.railway.app      ← 唯一對外公開的網址
                                       │
                                  Caddy 閘道
                    ┌──────────────────┴──────────────────┐
   /api/*  /ws  /health  /docs  /openapi.json          其他所有路徑
                    │                                     │
     FastAPI（Railway 內網，不對外）              前端（Vercel 正式網址）
                    │
          PostgreSQL（Railway 內網）
```

- 瀏覽器只看得到閘道那一個網址 → 同源 → cookie 是第一方的，`SameSite=Lax` 就夠
- **同源請求不經過 CORS**，所以後端的 `CORS_ORIGINS` 留空、`COOKIE_CROSS_SITE` 保持 `false`
- WebSocket 握手也是同源，cookie 自然帶上，身分與 room token 持有人比對（[R31]）都正常

⚠️ **前端自己的 `/api/*` Route Handlers（`NEXT_PUBLIC_DATA_ADAPTER=internal` 用的那組）
會被閘道蓋掉。** 接真後端時本來就用不到它們，但要先跟前端講。

---

## 2. Railway 步驟

### 2.1 帳號

1. 用 GitHub 帳號註冊 Railway，**完成 GitHub 驗證**。沒驗證的帳號是 Limited Trial，
   對外網路與可用 port 受限
2. Trial 是一次性 **$5 額度、30 天**（先到者為準），之後降為 Free plan：
   每月 $1、不累積 —— **撐不住常駐服務**。所以 Trial 要在發表日前 30 天內開，
   發表後要長期跑就換 §8
3. 專案設定 → **Region 選 `Southeast Asia (Singapore)`**（對台灣約 60–70ms）

### 2.2 PostgreSQL

同一個 project 裡 **New → Database → PostgreSQL**。不用開公開網址。

### 2.3 後端

1. New → GitHub repo → 選 `GuildHub-backend`，服務命名為 `backend`
2. Variables 設好 §3 的變數
3. Settings → Deploy → **Start Command** 貼上：

   ```
   python -m uvicorn app.main:app --host :: --port $PORT --proxy-headers --forwarded-allow-ips '*' --ws-ping-interval 20 --ws-ping-timeout 20
   ```

   **`--host ::` 不是 `0.0.0.0`。** Railway 內網只走 IPv6（官方 Caddy 模板的說明），
   綁 `0.0.0.0` 的話閘道從內網連不到後端。`::` 在 Linux 上同時收 IPv4

4. Settings → **Replicas 確認是 1**（見 §4）
5. Settings → 確認 **App Sleeping 是關的**
6. **不要** Generate Domain —— 後端不對外，只給閘道從內網連

### 2.4 Caddy 閘道

用 Railway 官方模板 [`caddy-reverse-proxy`](https://github.com/railwayapp-templates/caddy-reverse-proxy)
部署（New → Template → 搜 Caddy）。模板會在你的 GitHub 底下建一個 repo，裡面有
`Caddyfile` 與它自己的 `Dockerfile`。

> **那個 repo 跟這個 repo 分開。** `CLAUDE.md` 規定這個 repo 裡不能出現 Dockerfile，
> 閘道的東西一律放在模板生成的那個 repo，不要搬進來。

把模板的 `Caddyfile` 改成下面的分流規則（保留模板本身的全域設定，模板與這裡衝突時以
模板的語法為準，**分流的路徑清單以這裡為準**）：

```
{
	admin off
	auto_https off
}

:{$PORT} {
	@backend path /api/* /ws /health /docs /openapi.json
	handle @backend {
		reverse_proxy {$BACKEND_DOMAIN}:{$BACKEND_PORT} {
			header_up X-Forwarded-Proto https
		}
	}

	handle {
		reverse_proxy {$FRONTEND_URL} {
			header_up Host {upstream_hostport}
		}
	}
}
```

閘道服務的 Variables：

| 變數 | 值 | 備註 |
|---|---|---|
| `BACKEND_DOMAIN` | `${{backend.RAILWAY_PRIVATE_DOMAIN}}` | 用變數參照，不要手抄 |
| `BACKEND_PORT` | `${{backend.PORT}}` | 後端服務要有明確的 `PORT`，沒有就在後端設 `PORT=8000` |
| `FRONTEND_URL` | `https://<前端>.vercel.app` | **Vercel 的 production 網址**，不是 preview |

幾個細節：

- `auto_https off`：TLS 由 Railway 的邊緣終止，Caddy 收到的是 http
- `header_up X-Forwarded-Proto https`：同上，Caddy 自己看到的是 http，不覆寫的話
  FastAPI 會以為自己是 http
- `header_up Host {upstream_hostport}`：Vercel 依 Host 決定是哪個專案，
  不改成 `xxx.vercel.app` 會回 404
- WebSocket 的 `Upgrade` 由 `reverse_proxy` 自動處理，不用另外設定

最後在閘道服務 **Networking → Generate Domain**，拿到的網址就是**全案唯一的對外網址**。

### 2.5 前端（Vercel，免費方案）

前端照常部署在 Vercel，只是環境變數的兩個位址**都填閘道網址**：

```
NEXT_PUBLIC_APP_ENV=production
NEXT_PUBLIC_DATA_ADAPTER=guildhub
NEXT_PUBLIC_GUILDHUB_REST=https://<閘道>.up.railway.app
NEXT_PUBLIC_REALTIME_ADAPTER=guildhub
NEXT_PUBLIC_GUILDHUB_WS=wss://<閘道>.up.railway.app/ws
```

- **使用者只能從閘道網址進站。** 直接開 `xxx.vercel.app` 就回到跨站，Safari 登入會壞。
  簡報、QR code、群組裡貼的網址一律是閘道網址
- Vercel 每個 PR 的 preview 子網域**不接正式後端**，用 `NEXT_PUBLIC_REALTIME_ADAPTER=none`
  或接本機後端。**這件事要先跟前端講好**，否則第一次開 PR 就會有人以為後端壞了
- 前端的建置閘門**不檢查 `NEXT_PUBLIC_GUILDHUB_REST`**（前端 `docs/DEPLOY.md`），
  漏設要到頁面載入才拋錯

### 2.6 灌 schema 與假資料

Railway 的 Postgres 服務 → Variables 裡有 **`DATABASE_PUBLIC_URL`**（內網那個
`DATABASE_URL` 本機連不到）。把它設進本機環境，跑一次：

```bash
DATABASE_URL='<DATABASE_PUBLIC_URL 的值>' python tools/apply_sql.py --seed
```

（`--reset` 只在要砍掉重來時用）

---

## 3. 後端環境變數

| 變數 | 值 | 備註 |
|---|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | 用變數參照，不要手抄 |
| `SESSION_SECRET` | 隨機長字串 | 換掉會讓所有人登出 |
| `ROOM_TOKEN_SECRET` | 隨機長字串 | 同上 |
| `CORS_ORIGINS` | **空字串** | 同源不經過 CORS。**要設成空值，不要不設** —— 不設會套用預設的 localhost 清單（無害，但不是這個部署的意圖） |
| `COOKIE_CROSS_SITE` | `false`（或不設） | 同源不需要 `SameSite=None` |
| `PORT` | `8000` | 給閘道的 `BACKEND_PORT` 參照用 |

產生密鑰：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 什麼時候才用 `COOKIE_CROSS_SITE=true`

只有前後端**確定分屬不同網域**、而且接受 Safari 登入會壞的時候。這個專案在零費用
的前提下**不走這條**。開關與它的啟動檢查（CORS 有非本機來源卻沒開就炸掉）保留著，
是給日後真的有自訂網域時用的。

---

## 4. 六個會咬人的地方

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

**5. 後端綁 `::`，不是 `0.0.0.0`。**
Railway 內網只走 IPv6。症狀是閘道回 502，後端日誌什麼都沒有。

**6. 每次 deploy = 所有連線斷開。**
記憶體狀態歸零，大廳裡的人全部被踢出。**發表當天不要 push**
（前端 push 到 Vercel 不會斷後端連線，但會換掉頁面，一樣不要）。

---

## 5. 驗收（[D03]）

以下 `<網址>` 一律是**閘道網址**。

```bash
curl -i https://<網址>/health           # 200，body 是 {"status":"ok"}
curl -I https://<網址>/docs             # 200，OpenAPI 頁面
curl -I https://<網址>/                 # 前端經由閘道回得來（307 轉 /world 也算）
```

即時層要另外驗，`/health` 綠不代表 WebSocket 通：

```bash
python - <<'EOF'
import asyncio, json, websockets

async def main():
    async with websockets.connect("wss://<網址>/ws") as ws:
        print(json.loads(await ws.recv()))   # 應印出 {"t": "hello", ...}

asyncio.run(main())
EOF
```

併發（[D05] 用同一條，`--n 50`）：

```bash
python tools/run_swarm.py --url wss://<網址>/ws --n 40 --seconds 60
python tools/run_swarm.py --url wss://<網址>/ws --n 5 --idle      # 靜止時 pos 必須是 0
```

> ⚠️ **這會影響正在用這個環境的人**：40 個 bot 會出現在大廳裡。跑之前在群組說一聲，
> 不要在別人串接測試時跑。

**瀏覽器驗收（這一段才是 §0 第四列的證明，不能省）**：

| 裝置 | 步驟 | 預期 |
|---|---|---|
| 桌機 Chrome | 開閘道網址 → 登入 → 重新整理 | 仍是同一張名片（`GET /api/me` 200） |
| **Safari（Mac 或 iPhone）** | 同上 | 同上。**這一條過了才算同源真的成立** |
| Chrome 無痕 | 同上 | 同上 |
| 兩個裝置 | 同時進大廳走動 | 互相看得見，名字不是「訪客」 |
| 任一裝置 | 建專案 → 成軍 → 用密碼進房間 | WebSocket 連 `room:{id}` 成功（沒被 1008 關掉） |

**重啟驗證**：在 Railway 對後端按一次 Redeploy，等服務起來再跑一次上面全部。

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
