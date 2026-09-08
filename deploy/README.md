# 部署

> 2026-09-08 裁決：**正式部署走 Railway**，本目錄的 `guildhub.service` 與
> `deploy.sh` 保留為 VM 備援方案（見 §5），不刪除。
>
> 理由：後端剩下的工作有一半是部署，而 P1 同時要做備援錄影與簡報。
> Railway 省下裝 PostgreSQL、裝 Caddy、申請網域、設 systemd 與防火牆
> ——約三小時換成半小時。距發表日只剩兩週，這個交換划算。

---

## 0. 這個服務的三個硬條件

部署到哪裡都一樣，違反任何一條都會壞，而且**多半沒有錯誤訊息**：

| 條件 | 為什麼 | 違反的症狀 |
|---|---|---|
| **只能有一個 process** | 即時層的狀態全在記憶體（`SceneRegistry`／`PresenceStore`／`Broadcaster` 是 module-level 物件） | 大廳裡兩個人看不到彼此，隨機發生 |
| **不能睡** | 冷啟動等於台上等一分鐘 | 發表日第一個請求逾時 |
| **必須 HTTPS** | 前端在別的網域＝跨站，cookie 需要 `SameSite=None; Secure`；HTTPS 頁面也不能連 `ws://` | 登入回 200，然後每個 API 都 401 |

---

## 1. Railway 步驟

1. 用 GitHub 帳號註冊 Railway（**Trial 不需要信用卡**，一次性 $5 額度，30 天）
2. New Project → Deploy from GitHub repo → 選 `GuildHub-backend`
3. 專案設定 → **Region 選 `Southeast Asia (Singapore)`**（對台灣約 60–70ms）
4. 在同一個 project 裡 **New → Database → PostgreSQL**
5. 後端服務的 Variables 裡設好 §3 那張表的變數
   （`DATABASE_URL` 用 Railway 的變數參照指向 Postgres 服務，不要手抄）
6. Settings → Deploy → **Start Command** 貼上：

   ```
   python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*' --ws-ping-interval 20 --ws-ping-timeout 20
   ```

7. Settings → **Replicas 確認是 1**（見 §2）
8. Settings → 確認 **App Sleeping 是關的**
9. Networking → Generate Domain，拿到 `xxx.up.railway.app`
10. 第一次部署完成後，灌 schema 與假資料：把 Railway 的 `DATABASE_URL`
    設進本機環境，跑一次

    ```bash
    python tools/apply_sql.py --seed
    ```

    （`--reset` 只在要砍掉重來時用）

---

## 2. 五個會咬人的地方

**1. Start Command 絕對不能有 `--workers`。**
理由見 §0 第一列。Railway 不會攔你，uvicorn 也不會報錯。

**2. Replicas 保持 1。**
Railway 的水平擴容按一下就開，按下去就是多一個互相看不見的世界。
**這件事要寫進交接說明**，否則日後有人「順手優化」。

**3. `$PORT` 是平台指定的**，不能寫死 8000。`run.sh` 已經改成
`${PORT:-8000}`，本機行為不變。

**4. `--proxy-headers` 必加，而且要 `--forwarded-allow-ips '*'`。**
uvicorn 預設只信任 `127.0.0.1` 送來的 `X-Forwarded-*`，而平台代理的來源 IP
不固定。沒有它，FastAPI 會以為自己是 http。

**5. 每次 deploy = 所有連線斷開。**
記憶體狀態歸零，大廳裡的人全部被踢出。**發表當天不要 push。**

---

## 3. 環境變數

| 變數 | 值 | 備註 |
|---|---|---|
| `DATABASE_URL` | 參照 Postgres 服務 | 用 Railway 的變數參照，不要手抄 |
| `SESSION_SECRET` | 隨機長字串 | 換掉會讓所有人登出 |
| `ROOM_TOKEN_SECRET` | 隨機長字串 | 同上 |
| `CORS_ORIGINS` | 前端的**正式**網域 | 逗號分隔，**不接受 `*`** |
| `COOKIE_CROSS_SITE` | `true` | 前端在別的網域時必須開 |
| `FORWARDED_ALLOW_IPS` | `*` | 只在用 `run.sh` 啟動時需要 |

產生密鑰：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### ⚠️ `CORS_ORIGINS` 與前端的 preview 部署

`CORS_ORIGINS` **不接受萬用字元**（帶 cookie 的跨源請求瀏覽器規定不能用 `*`，
`app/config.py` 會在啟動時直接炸掉）。

Vercel 每個 PR 會生一個新的 preview 子網域，**那些網域一律連不到正式後端**。
要嘛只讓 production 網域接正式後端、preview 接本機，要嘛每次手動加。
**這件事要先跟前端講好**，否則第一次開 PR 就會有人以為後端壞了。

GitHub Pages 只有一個固定網址，沒有這個問題。

---

## 4. 驗收（[D03]）

```bash
curl https://<你的網域>/health          # 200
curl https://<你的網域>/docs            # OpenAPI 頁面打得開
```

即時層要另外驗，`/health` 綠不代表 WebSocket 通：

```bash
python tools/fake_client.py --url wss://<你的網域>/ws     # 收得到 hello
python tools/run_swarm.py --url wss://<你的網域>/ws -n 40 # 40 併發
```

**重啟驗證**：在 Railway 按一次 Redeploy，等服務起來再跑一次上面四條。

---

## 5. 備援方案：VM + systemd

本目錄的 `guildhub.service` 與 `deploy.sh` 仍然可用，兩種情況會用到：

- Railway 額度用完或出狀況
- 發表後要長期跑（Oracle Cloud Always Free 東京，永久免費）

```bash
sudo cp deploy/guildhub.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now guildhub
```

VM 路線額外要做的：裝 PostgreSQL、裝 Caddy（自動 HTTPS，設定只要兩行）、
準備一個網域（DuckDNS 免費即可）。

```
api.你的網域
reverse_proxy localhost:8000
```

不要用 nginx —— 要自己處理 certbot 與 WebSocket 的 `Upgrade` header，
多半小時而且容易漏掉 WS 那段。
