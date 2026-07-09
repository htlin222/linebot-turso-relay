# linebot-turso-relay

> 🇬🇧 English: [README.md](README.md)

一個 LINE bot，把你轉發給它的每一則訊息都抓下來，並將 metadata（寄件者、時間戳、
型別、內容）中繼寫入 [Turso](https://turso.tech)（libSQL）資料庫。跑在 Cloudflare
Workers 上——免費方案對個人用量綽綽有餘。二進位附件（圖片／影片／音訊／檔案）會串流
到 R2 bucket。

名字就是它在做的事：**LINE bot → Turso 中繼（relay）**。其餘的一切——webhook、
R2 封存、Claude Code skills——都是圍繞這個核心概念延伸出來的。

> **命名說明：** 這個 repo 附帶一份*參考部署*，其 Worker 叫 `lizard-the-linebot`、
> bot 叫 `lizard-inbox`、DB 叫 `lizard`。那些只是範例名稱——你自己部署時請換成
> 自己的。下面的指令都用參考名稱，方便你有具體的東西可以照抄。

> 📓 **專案筆記：** 見 [`docs/`](docs/00-overview.md)，裡面有 IMRaD 體例的筆記，
> 涵蓋協定的眉角、schema 選擇、runtime 取捨、quote-reply 調查，以及可攜式 skill 設計。

> **關於轉發訊息：** LINE 在使用者轉發訊息時*不會*保留原始寄件者。bot 只會看到是
> *你*送的；`source_user_id` 永遠是轉發者本人。原始寄件者資訊（如果有的話）只會以
> 使用者自己打的行內文字形式殘留。

---

## 為什麼做這個

LINE 是個黑盒子。有用的東西一直往裡面掉——朋友提的某個日期、順口說的截止期限、
想「之後再看」的文章、另一半請你順路買的東西——然後就沉下去了。搜尋很淺、*Keep*
很難用，而且沒有任何東西能以「另一個程式讀得懂」的形式離開 LINE。

**這個 bot 把 LINE 變成一個你自己擁有的收件匣。** 把任何東西（文字、貼圖、位置、
檔案、圖片）轉發給你的 bot；webhook 會在約 2 秒內把一列（row）寫進 Turso（libSQL）
資料庫。LINE 那端只負責抓取——DB 才是整合的介面。

**一旦進了 SQL，agent 就能讀它。** 這個 repo 附帶兩個 Claude Code skills：
[`line-inbox`](.claude/skills/line-inbox/SKILL.md)（讀取端——搜尋／列出／統計封存）
與 [`line`](.claude/skills/line/SKILL.md)（寫入端——把顯示名稱解析成 userId 並把訊息
推回去）。它們直接查詢資料表，所以像是*「把我這週轉發的日期抓出來加進行事曆」*或
*「把我週五轉發裡的待辦事項變成 todo」*這類 prompt 都能端到端跑通。agent 會從各列
裡萃取日期、名字、待辦事項，再交給你接好的行事曆／todo 工具。

你照舊用 LINE——你的轉發只是變得可查詢、agent 讀得懂、而且是你的。

```
LINE 轉發  →  webhook  →  你自己的 DB  →  agent  →  行事曆 / todos / 搜尋
  (抓取)                    (你的資料)             (行動)
```

---

## 運作方式

```
┌─────────┐   轉發     ┌──────────────┐   webhook   ┌────────────────────┐
│  LINE   ├───────────▶│ LINE 平台     ├────────────▶│ Cloudflare Worker  │
│  手機   │            │              │   (POST)    │                    │
└─────────┘            └──────────────┘             │                    │
      ▲                       │                     │ 1. 驗 HMAC          │
      │  「已收到」回覆         │                     │ 2. 解析顯示名稱      │
      │  (只有在群組被          │                     │    (LINE API)       │
      │   @-mention 時)        │  reply API          │ 3. INSERT 一列       │
      └───────────────────────┤◀────────────────────┤ 4. 被 mention 才 ack │
                                                     └─────────┬──────────┘
                                                               │
                                                               ▼
                                                     ┌────────────────────┐
                                                     │ Turso DB (libSQL)  │
                                                     │  資料表: messages   │
                                                     └────────────────────┘
```

每個 `POST /webhook`：

1. 讀取原始 body 與 `x-line-signature` header。
2. 計算 `base64(HMAC-SHA256(channel_secret, raw_body))`。不符就回 `401`。
3. 對 payload 裡每個 `message` event：
   - 把 event 對映成一列（text/sticker/file/location 有型別專屬欄位；所有東西也都以 JSON 塞進 `raw_payload`）。
   - 呼叫 `GET /v2/bot/profile/{userId}` 解析寄件者的顯示名稱。
   - `INSERT … ON CONFLICT(webhook_event_id) DO NOTHING`——對 LINE 的重送具冪等性。
   - 若 bot 被 @-mention（僅限群組／聊天室），透過 `POST /v2/bot/message/reply` 送出 ack 回覆（在回 200 之後，經由 `ctx.waitUntil`）。
4. 回傳 `200 ok`。

未知的 event 型別（follow、unfollow、postback 等）會被靜默略過。

### 什麼會被存下來、什麼時候會回覆

每一則打到 webhook 的訊息都會被存進 DB（完整封存）。ack 回覆是分開管制的——只有
bot 在群組／聊天室被明確 @-mention 時才會觸發。DM 也保持沉默，所以除非被直接召喚，
bot 絕不出聲。

| 情境 | 有存？ | 有回？ |
|---|---|---|
| 1 對 1 DM | ✅ | ❌ 沉默 |
| 群組／聊天室，任何訊息 | ✅ | ❌ 沉默 |
| 群組／聊天室，@bot 被 mention | ✅ | ✅ |
| 群組／聊天室，`@all` | ✅ | ❌ 沉默（刻意——不洗群） |

回覆閘門是 `src/index.ts` 底部的 `shouldReply()`——檢查 `event.message.mention.mentionees[].isSelf`。

要能收到群組 event，LINE Console → Messaging API 分頁裡的 **Allow bot to join group chats** 必須設為 **Enabled**。

### 二進位附件 → R2

對 `image`、`video`、`audio`、`file` 訊息，worker 還會從 LINE 拉出位元組
（`GET /v2/bot/message/{id}/content`）並塞進一個 R2 bucket（參考部署叫它
`lizard-attachments`）——以 `YYYY-MM/<messageId>` 為 key，並保留 `Content-Type`。
DB 那列的 `r2_key` 欄位會拿到這個 key。LINE 只保存內容約 7 天，所以這是一次性抓取。
下載跑在 `ctx.waitUntil()` 裡，所以 webhook 仍在數毫秒內回 200。

### Quote-reply（回覆某則先前的訊息）

當有人回覆訊息 X、而你的訊息落進我們的封存時，LINE 會在 payload 裡帶上
`quotedMessageId: X.message_id`。我們把它存進 `quoted_message_id`，若 X 也在我們的
封存裡（現在通常都在，因為群組什麼都存）你就能 JOIN 它們。skill 透過這個指令暴露：

```bash
python3 .claude/skills/line-inbox/scripts/inbox.py mentions
```

——@-mention 的各列，並把被引用的原文以 LEFT JOIN 帶進來。完整封存時代之前的原文會
以 NULL 的 `reply_to_*` 欄位出現，且無法還原（LINE 沒有公開 API 可以撈任意歷史文字）。

---

## 快速開始（端到端）

一份 [`Makefile`](Makefile) 把從 clone 到 webhook 上線的整條路徑都包了起來。
執行 `make help` 看所有 target。順順的路徑：

```bash
make install         # pnpm install
make env             # 從 .example 產生 .env / .dev.vars
#   → 用你的 4 個 secret 編輯 .env，然後：
make db              # 建立 Turso DB + 套用 schema/*.sql
make secrets         # 把 4 個 secret 推進 Cloudflare
make r2              # 建立 R2 附件 bucket
make deploy          # wrangler deploy
make url             # 印出要貼進 LINE Console 的 webhook URL
```

接著把 `<worker-url>/webhook` 貼進 **LINE Console → Messaging API → Webhook URL**，
按 **Verify**，然後轉發一則訊息測試。`make tail` 會串流即時 log。

---

## 部署後會有什麼

設定完你會有類似這樣的東西（名稱是參考部署的——換成你自己的）：

| 東西 | 範例 |
|---|---|
| Worker | `https://lizard-the-linebot.<你的-cf-subdomain>.workers.dev` |
| Webhook 端點 | `POST /webhook` |
| 健康檢查 | `GET /` → `lizard is alive` |
| Turso DB | `lizard`（挑一個離你近的 region，例如東京 `aws-ap-northeast-1`） |
| R2 bucket | `lizard-attachments`（放圖片／影片／音訊／檔案的位元組，key 為 `YYYY-MM/<messageId>`） |
| LINE channel | 你自己的 `lizard-inbox`（LINE 會配一個 `@<bot-id>` + 一組數字 channel ID） |
| Cloudflare secrets | `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`、`TURSO_DATABASE_URL`、`TURSO_AUTH_TOKEN` |

本機 secret 放在 `.env` 與 `.dev.vars`（兩者都被 gitignore）。

---

## 日常使用

從 LINE app 把任何訊息轉發給你的 bot。bot 保持沉默（DM 不再自動 ack），但那一列會在
約 2 秒內落進 Turso。要看落了什麼：

```bash
# 最近 20 筆
turso db shell lizard "SELECT id, message_type, substr(text,1,80) AS preview, datetime(line_timestamp_ms/1000,'unixepoch','+8 hours') AS at_tw FROM messages ORDER BY id DESC LIMIT 20;"

# 依型別統計
turso db shell lizard "SELECT message_type, COUNT(*) AS n FROM messages GROUP BY message_type ORDER BY n DESC;"

# 今天（台灣時間）
turso db shell lizard "SELECT message_type, text FROM messages WHERE date(line_timestamp_ms/1000,'unixepoch','+8 hours') = date('now','+8 hours');"

# 全文搜尋
turso db shell lizard "SELECT id, datetime(line_timestamp_ms/1000,'unixepoch','+8 hours') AS at, text FROM messages WHERE text LIKE '%KEYWORD%' ORDER BY id DESC;"
```

或者直接在 Claude Code 用 `line-inbox` skill 讓它幫你查。

---

## 除錯

**即時串流 worker log：**

```bash
pnpm dlx wrangler tail      # 或：make tail
```

這會串流每個打到 worker 的請求——包含 LINE 的 webhook 呼叫——連同狀態碼、例外，以及
你的 `console.log`／`console.error` 輸出。轉發測試訊息時讓它開著。

**Cloudflare dashboard：** <https://dash.cloudflare.com/> → Workers → 你的 worker →
**Observability** 有近 24 小時的請求 log 與 metrics。

**常見故障模式：**

| 症狀 | 可能原因 | 修法 |
|---|---|---|
| LINE 顯示「Webhook URL verification failed」 | Worker 連不到，或對空 `events` payload 回非 2xx | 按 Verify 時開 `pnpm dlx wrangler tail`；應看到 `200 ok`。若 401，CF 裡的 channel secret 跟 LINE 對不上。 |
| 轉發的訊息在 DB 裡沒落任何列 | `Use webhook` 開關關著，或 `Auto-response` 被重新開啟正在吃掉 event | 重新檢查 LINE Console → Messaging API 的開關。Tail log 確認請求到底有沒有打到我們。（現在沒有 DM 自動 ack——請用 DB 列確認，別靠回覆。） |
| 列有落進 DB 但群組 @-mention 回覆沒觸發 | Turso 寫入成功但 reply API 呼叫失敗——或那則訊息根本不是 @-mention | `wrangler tail` → 找 `reply failed`。確認 payload 裡有 `mention.mentionees[].isSelf === true`。 |
| 你自己簽的請求卻 `✗ invalid signature`（401） | 簽名與送出之間 body 位元組不一致（多餘換行、編碼） | 用 `printf '%s'` 而非 `echo`；簽*原始*位元組。 |

**輪換 secret**（LINE channel secret 外洩等）：

```bash
# 1. 在來源真相處重新發行
#    LINE：Console → Basic settings → Channel secret → Issue
#    Turso：turso db tokens create lizard --revoke   # 讓舊的失效，印出新的
# 2. 更新本機 .env
# 3. 推進 Cloudflare：
printf '%s' "NEW_VALUE" | pnpm dlx wrangler secret put LINE_CHANNEL_SECRET
```

---

## 調整

### 換 ack 回覆

`src/index.ts`——搜尋 ack 字串。你放進去的字串就是 bot 被 @-mention 時回給 LINE 的內容。上限約 2000 字。

### 抓額外欄位

`src/types.ts` 加型別、`src/extract.ts` 讀進一個欄位、`src/db.ts` 的 `INSERT_SQL` 寫入。用 `schema/` 裡的新 migration 加欄位：

```sql
-- schema/004_add_xxx.sql
ALTER TABLE messages ADD COLUMN xxx TEXT;
```

用 `turso db shell lizard < schema/004_add_xxx.sql` 套用。`raw_payload` 欄位已經有完整
event JSON，所以歷史列可以用 `UPDATE … SET xxx = json_extract(raw_payload, '$.…')` 回填。

### 客製附件封存

R2 封存路徑在 `src/index.ts` 的 `archiveContent()`。常見客製：

- **不同 key 形狀**——改 `${yyyymm}/${messageId}` 那行。注意：R2 list 操作以 prefix 過濾，所以用日期分桶能讓列表便宜。
- **依型別路由**——在呼叫 `archiveContent` 前用 `row.message_type` 做 gate（例如不在乎音訊就跳過）。
- **公開存取**——R2 bucket 預設私有。可把 bucket 翻成公開（`wrangler r2 bucket dev-url enable`），或從另一條 worker route 簽發 signed URL。
- **換整個儲存後端**——把 `env.ATTACHMENTS.put` 換成 HTTPS API 呼叫（Resend／Gmail／Drive）。函式形狀不變。

### 換 worker region／搬到離使用者更近

Workers 在每個 Cloudflare edge 上跑——沒東西要改。Turso DB 的 region 才是延遲下限；
若你搬國家，用 `turso db create lizard2 --location <new>` 重建並遷移。

### 只允許你自己的 userId

現在任何 friend 了 bot 的人都能寫你的 DB（風險低：bot URL 不公開，但還是）。在
`src/index.ts` 的 `for (const event…)` 迴圈最上面加：

```ts
const ALLOWED = new Set(["U_your_user_id_here"]);
if (msg.source.userId && !ALLOWED.has(msg.source.userId)) continue;
```

從任何已經落地的列取得你的 userId：`SELECT DISTINCT source_user_id FROM messages;`。

---

## 重新部署

```bash
pnpm dlx wrangler deploy      # 或：make deploy
```

就這樣。worker 上傳（約 2 秒），Cloudflare 原子式切換流量，零停機。要 rollback：

```bash
pnpm dlx wrangler deployments list           # 找到前一個 version ID
pnpm dlx wrangler rollback <version-id>
```

---

## 本機開發

```bash
make install                         # pnpm install
make env                             # cp .example 檔，然後填 4 個值
pnpm dev                             # http://localhost:8787   (或：make dev)
```

送一個簽好名的測試請求：

```bash
make dev-test        # 簽 BODY='{"destination":"U0","events":[]}' 並 curl localhost
```

或手動：

```bash
SECRET=$(grep '^LINE_CHANNEL_SECRET=' .env | cut -d= -f2-)
BODY='{"destination":"U0","events":[]}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -binary | base64)
curl -i -X POST http://localhost:8787/webhook \
  -H "x-line-signature: $SIG" \
  -H 'content-type: application/json' \
  -d "$BODY"
# 預期：HTTP/1.1 200 OK
```

注意本機 dev 打的是*真的* Turso DB。要用獨立的 dev DB，建一個
（`turso db create lizard-dev`）並在 `.dev.vars` 覆寫 `TURSO_DATABASE_URL`。

---

## 專案結構

```
src/index.ts        fetch handler + 路由
src/line.ts         HMAC 驗證、profile 查詢、回覆
src/db.ts           Turso client + INSERT
src/extract.ts      LINE event → DB 列
src/types.ts        精簡的 LINE webhook 型別
schema/             SQL migrations（用 turso db shell <db> < file 套用）
wrangler.toml       Worker 設定（無 secret）
Makefile            端到端 設定 / 部署 / 除錯 target
.env / .dev.vars    本機 secret（gitignored）
.claude/skills/     line-inbox（讀）+ line（寫）Claude Code skills
docs/               IMRaD 專案筆記
```

---

## 成本（撰寫當下）

- **Cloudflare Workers 免費**：每日 10 萬次請求。個人轉發器差得遠。
- **Turso 免費**：9 GB 儲存、每月 10 億次 row 讀 / 2500 萬次寫。同樣——很寬裕。
- **LINE Messaging API 免費方案**：每月 200 則 *push* 訊息。回覆（我們用的就是這個）不計入。所以免費。

---

## 授權

[MIT](LICENSE)。
