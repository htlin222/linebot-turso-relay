---
name: line-inbox
description: Query the user's personal LINE message archive — every message they forwarded to the lizard-inbox LINE bot is stored in a Turso DB by linebot-turso-relay. Use when the user asks "what did I forward", "search my LINE archive/inbox", "latest line message", "上週 forward 給 lizard 的訊息", "我存到 lizard 的東西", "翻一下我的 LINE 收藏", "what's in my line bot db", or wants to list/search/count/inspect captured LINE messages.
allowed-tools: Bash(python3 *)
---

# LINE Inbox

Query the personal LINE message archive captured by [linebot-turso-relay](https://github.com/htlin222/linebot-turso-relay). Storage is a Turso (libSQL) database; reads go via Turso's HTTP pipeline API — no extra Python deps.

## Path conventions

`${CLAUDE_SKILL_DIR}` = directory holding this `SKILL.md`. Resolve once per shell:

```bash
export CLAUDE_SKILL_DIR=<path reported by the Skill tool's "Base directory">
```

Credentials are looked up in this order:

1. `LINE_INBOX_ENV=/path/to/.env` (explicit override)
2. **Skill-local `.env`** at `${CLAUDE_SKILL_DIR}/.env` (gitignored; ships with the skill — copy/move it anywhere and creds go with it)
3. Project `.env` at `<repo>/.env` (resolved relative to this script's location) (fallback)
4. Shell env vars `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` (last resort)

File paths win over shell env vars on purpose — the user has another Turso DB exported globally, so reading env first would silently hit the wrong DB.

The skill-local `.env` only needs the Turso pair. The `pull` subcommand additionally wants `LINE_CHANNEL_ACCESS_TOKEN` — it's read from `LINE_INBOX_ENV` → skill `.env` → project `.dev.vars` → project `.env` → shell env (first hit wins). The skill folder ships its own `.gitignore` to keep `.env`, `__pycache__/`, and the local `media/` cache out of any repo it lives in.

## Invocation

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" [--json] <subcommand> [args]
```

Subcommands: `latest`, `today`, `search`, `count`, `since`, `get`, `mentions`, `attachments`, `pull`, `sql`. Default output is a fixed-width table; `--json` emits JSON.

## References (load on demand)

- [references/schema.md](references/schema.md) — `messages` table columns, type-specific fields, what's in `raw_payload`

## Common playbooks

### Latest N (default 10)

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" latest -n 5
```

### Today (Asia/Taipei)

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" today
```

### Search text body

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" search "keyword"
# LIKE %keyword%, capped at -n (default 20)
```

### Time window

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" since "-7 days"      # relative
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" since "2026-05-01"   # absolute
```

### Counts by type

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" count
```

### @-mentions of the bot, with quoted originals

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" mentions -n 20
```

Returns rows where `is_self_mention=1` (i.e. the bot was @-mentioned), LEFT-JOINed against the message they reply-quote (matched on `quoted_message_id` + `source_group_id`). `reply_to_*` columns are NULL when the quoted original isn't in our archive (e.g. it predates the full-group-archive policy).

### Attachments archived to R2

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" attachments              # all binary-type rows
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" attachments --missing    # only rows where r2_key IS NULL
```

`image`/`video`/`audio`/`file` messages have their bytes pulled into the `lizard-attachments` R2 bucket at key `YYYY-MM/<messageId>` — `r2_key` on the row points at the object. `--missing` finds rows where the download didn't land (transient failure, or the message arrived before R2 archival was deployed). Within the LINE 7-day content-retention window, missing rows can be retro-fetched; after that they're gone.

### Pull binary content to the local cache

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" pull 1225            # one message by DB id
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" pull --since "-7 days"  # mirror a window
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" pull --open 1225     # + open in default app
```

`pull` downloads to `${CLAUDE_SKILL_DIR}/media/<YYYY-MM>/<messageId>.<ext>` (gitignored) and is **idempotent** — an already-cached file is returned as `source: "cache"` without re-fetching, so this is how you "keep one local copy". Fetch order per message:

1. **LINE content API** (`api-data.line.me`, needs `LINE_CHANNEL_ACCESS_TOKEN`) — fast, but only works inside LINE's ~7-day retention window. `source: "line-api"`.
2. **R2 fallback** (`pnpm dlx wrangler r2 object get`, needs Cloudflare auth) — authoritative and all-time, used when LINE 404s (message older than 7 days). `source: "r2"`.

So recent forwards pull instantly via LINE; older ones need working R2 access. **R2 needs Cloudflare credentials that aren't in the skill `.env`** — if wrangler auth is stale you'll see `Invalid access token [code: 9109]`; run `pnpm dlx wrangler login` (or drop a Cloudflare API token where wrangler can see it). Until then, only ≤7-day images are pullable. Bulk `pull` never aborts on one failure — each row reports its own `source: "error"` with the reason.

**Note:** the relay worker already archives every attachment to R2 automatically on arrival — there is no way to auto-save to *this machine* (the worker runs on Cloudflare). `pull` is the read-side mirror of that durable R2 copy onto local disk.

To grab R2 bytes by hand instead — `<bucket>/<key>` is a single slash-joined arg, and `--remote` is required to hit the production bucket (otherwise wrangler looks in its local cache):

```bash
pnpm dlx wrangler r2 object get "lizard-attachments/$R2_KEY" --remote --file ./local-name
# or pipe to stdout:
pnpm dlx wrangler r2 object get "lizard-attachments/$R2_KEY" --remote --pipe > out.bin
```

### Full row (incl. raw_payload JSON)

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" --json get 7
```

### Ad-hoc SELECT

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/inbox.py" sql \
  "SELECT message_type, COUNT(*) FROM messages WHERE date(line_timestamp_ms/1000,'unixepoch','+8 hours') >= '2026-05-01' GROUP BY 1"
```

`sql` accepts only one statement, no parameter binding (use single quotes inside the query). Read-only is not enforced — be careful.

## Output shape

Table mode prints a header + dashed underline + rows, columns left-aligned. JSON mode prints `[{col: val, ...}, ...]`; integers/floats are typed, NULL → Python `None` → JSON `null`.

## Errors

- `no creds: ...` — `.env` missing or lacks `TURSO_*` lines.
- `HTTP 401: ...` — token expired/revoked. Reissue: `turso db tokens create lizard --revoke`.
- `sql error: no such table: messages` — wrong DB. Almost always means a stray `TURSO_DATABASE_URL` env var slipped through; check `LINE_INBOX_ENV`.
- `(no rows)` — query ran fine, just nothing matched.
