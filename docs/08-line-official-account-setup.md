# Setting up the LINE Official Account (step by step)

The README assumes you already have a LINE channel with a Channel secret and
a Channel access token. This doc is the missing prequel: how to create the
LINE Official Account (OA) and enable the Messaging API so those two secrets
exist.

> 🇹🇼 中文版在本檔下半部 → [跳到中文](#從零開設-line-官方帳號逐步教學)

> **The one thing that trips everyone up:** LINE has *two* separate consoles
> and you bounce between them.
> - **LINE Official Account Manager** (`manager.line.biz`) — the account
>   itself, and the response settings.
> - **LINE Developers Console** (`developers.line.biz`) — the Messaging API,
>   the secrets, and the webhook URL.

## A. Prerequisites

- A personal LINE account on your phone (to log in, and to friend the bot for testing).
- A **LINE Business ID** — sign up with your LINE account or an email.

## B. Create the Official Account (OA)

1. Go to <https://www.linebiz.com/> → **Create a free account** (or straight to `manager.line.biz`).
2. Log in with your LINE Business ID.
3. Fill in **account name, category, region**.
4. Submit. The OA is created. An **unverified account** (grey shield) is fine — free, no review, works with the Messaging API.

## C. Enable the Messaging API

1. In **LINE Official Account Manager** (`manager.line.biz`) → pick your account → **Settings** → **Messaging API** (left sidebar).
2. Click **Enable Messaging API**.
3. Pick a **Provider**, or create one (your name / org name).
4. Agree and confirm. This links the OA to a channel in the Developers Console.

## D. Grab the two secrets (Developers Console)

1. Open <https://developers.line.biz/console/> → your **Provider** → your **channel**.
2. **Basic settings** tab → copy **Channel secret** → this is `LINE_CHANNEL_SECRET`.
3. **Messaging API** tab → **Channel access token (long-lived)** → click **Issue** → copy → this is `LINE_CHANNEL_ACCESS_TOKEN`.

Put both into `.env` (the other two, `TURSO_*`, come from `make db`).

## E. Turn off auto-reply, turn on webhook (the critical step)

LINE ships with canned auto-replies that **swallow the webhook event**. You must change this:

**In OA Manager → Settings → Response settings:**

| Setting | Value |
|---|---|
| Response mode | **Bot / Chatbot** |
| Auto-reply messages | **Off** |
| Greeting message | Off (optional) |
| Webhook | **On** |

**In Developers Console → Messaging API tab:**

| Setting | Value |
|---|---|
| Webhook URL | `https://<your-worker>.workers.dev/webhook` (needs `make deploy` first) |
| Use webhook | **On** |
| Allow bot to join group chats | **On** (required to receive group messages) |

After filling the Webhook URL, click **Verify** — you should get a success (the worker returns `200 ok` for an empty events payload).

## F. Add as friend and test

1. Find the bot's **QR code / LINE ID (`@xxxx`)** in the Messaging API tab or OA Manager.
2. Scan it from your phone's LINE to add the bot.
3. Forward any message. Confirm it landed with `make tail` (live logs) or `line-inbox latest` (a row in the DB).

## How this maps to the project flow

```
B–C   create OA + enable Messaging API
D     copy LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN  →  .env
      (TURSO_DATABASE_URL / TURSO_AUTH_TOKEN come from `make db`)
make deploy → get the worker URL
E     paste <worker-url>/webhook into Developers Console, click Verify
F     friend the bot, forward, verify
```

---

# 從零開設 LINE 官方帳號（逐步教學）

README 假設你已經有一個帶 Channel secret 與 Channel access token 的 LINE
channel。這份文件補上前傳：怎麼建立 LINE 官方帳號（OA）並開通 Messaging API，
好讓那兩把金鑰生出來。

> **最多人卡住的一點：** LINE 有*兩個*不同後台，你會來回切換。
> - **LINE Official Account Manager**（`manager.line.biz`）— 帳號本身、回應設定。
> - **LINE Developers Console**（`developers.line.biz`）— Messaging API、金鑰、webhook URL。

## A. 前置作業

- 手機上一個**個人 LINE 帳號**（用來登入，之後加 bot 好友測試）。
- 一個 **LINE Business ID** — 用 LINE 帳號或 email 註冊。

## B. 建立官方帳號（OA）

1. 開 <https://www.linebiz.com/tw/> → **免費開設帳號**（或直接到 `manager.line.biz`）。
2. 用 LINE Business ID 登入。
3. 填**帳號名稱、類別、地區**。
4. 送出，OA 建好。**未認證帳號**（灰盾）即可 — 免費、免審核，且能用 Messaging API。

## C. 開通 Messaging API

1. 進 **LINE Official Account Manager**（`manager.line.biz`）→ 選你的帳號 → **設定** → 左側 **Messaging API**。
2. 點 **啟用 Messaging API**。
3. 選一個 **Provider（服務提供者）**，沒有就新建（填你的名字／組織名）。
4. 同意條款並確認。這會把 OA 綁到 Developers Console 的一個 channel。

## D. 拿兩把金鑰（Developers Console）

1. 開 <https://developers.line.biz/console/> → 你的 **Provider** → 你的 **channel**。
2. **Basic settings** 分頁 → 複製 **Channel secret** → 這就是 `LINE_CHANNEL_SECRET`。
3. **Messaging API** 分頁 → **Channel access token (long-lived)** → 按 **Issue** → 複製 → 這就是 `LINE_CHANNEL_ACCESS_TOKEN`。

兩把都填進 `.env`（另外兩把 `TURSO_*` 由 `make db` 產生）。

## E. 關自動回覆、開 webhook（關鍵步驟）

LINE 預設有罐頭自動回覆，會**吃掉 webhook event**，務必改掉：

**在 OA Manager → 設定 → 回應設定：**

| 設定 | 值 |
|---|---|
| 回應模式 | **Bot / 聊天機器人** |
| 自動回應訊息 | **關** |
| 加入好友的歡迎訊息 | 關（可選） |
| Webhook | **開** |

**在 Developers Console → Messaging API 分頁：**

| 設定 | 值 |
|---|---|
| Webhook URL | `https://<your-worker>.workers.dev/webhook`（要先 `make deploy`） |
| Use webhook | **開** |
| Allow bot to join group chats（允許加入群組） | **開**（要收群組訊息就必須開） |

填好 Webhook URL 後按 **Verify** — 應該成功（worker 對空 events payload 回 `200 ok`）。

## F. 加好友並測試

1. 在 Messaging API 分頁或 OA Manager 找 bot 的 **QR code / LINE ID（`@xxxx`）**。
2. 手機 LINE 掃碼加好友。
3. 轉發任一訊息。用 `make tail`（即時 log）或 `line-inbox latest`（DB 落了一列）確認。

## 對應到本專案流程

```
B–C   建 OA + 開 Messaging API
D     複製 LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN  →  .env
      (TURSO_DATABASE_URL / TURSO_AUTH_TOKEN 由 `make db` 產生)
make deploy → 拿到 worker 網址
E     把 <worker-url>/webhook 貼進 Developers Console，按 Verify
F     加 bot 好友、轉發、驗證
```
