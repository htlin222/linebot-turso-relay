#!/usr/bin/env python3
"""Query the linebot-turso-relay Turso DB from anywhere."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_ENV = SKILL_ROOT / ".env"
# Resolves to <repo-root>/.env when the skill lives at <repo>/.claude/skills/line-inbox/.
# Path(__file__).resolve() follows symlinks, so this stays correct even when the
# skill folder is symlinked in from another repo.
PROJECT_ENV = SKILL_ROOT.parents[2] / ".env"
# .dev.vars sits next to the project .env and holds the LINE channel token the
# relay worker uses; `pull` reuses it to hit LINE's content API.
PROJECT_DEV_VARS = SKILL_ROOT.parents[2] / ".dev.vars"
# Local cache for pulled binary content — mirrors the R2 YYYY-MM/<messageId>
# layout. Gitignored; keeps a durable local copy so re-views are instant.
MEDIA_ROOT = SKILL_ROOT / "media"


def load_creds() -> tuple[str, str]:
    """Resolve URL + token. Precedence:

    1. LINE_INBOX_ENV=/path/to/.env (explicit override)
    2. Skill-local .env (next to SKILL.md) — makes the skill portable
    3. Project .env at <repo>/.env (resolved relative to this script) — fallback
    4. Shell env vars (TURSO_*) — last resort

    File paths win over shell env vars because the user has another
    Turso DB exported globally; reading env first would hit the wrong DB.
    """
    candidates: list[pathlib.Path] = []
    override = os.environ.get("LINE_INBOX_ENV")
    if override:
        candidates.append(pathlib.Path(override))
    candidates += [SKILL_ENV, PROJECT_ENV]

    for env_path in candidates:
        if not env_path.exists():
            continue
        env: dict[str, str] = {}
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
        url = env.get("TURSO_DATABASE_URL", "")
        token = env.get("TURSO_AUTH_TOKEN", "")
        if url and token:
            return _to_https(url), _op_resolve(token)

    url = os.environ.get("TURSO_DATABASE_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if url and token:
        return _to_https(url), token
    sys.exit(
        f"no creds: put TURSO_DATABASE_URL + TURSO_AUTH_TOKEN in "
        f"{SKILL_ENV} or {PROJECT_ENV}, or set LINE_INBOX_ENV=/path/to/.env"
    )


def _op_resolve(value: str) -> str:
    """Resolve a 1Password `op://` secret reference via the `op` CLI; plain values pass through."""
    if not value.startswith("op://"):
        return value
    try:
        r = subprocess.run(["op", "read", "--no-newline", value],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        sys.exit(f"cannot resolve {value} with 1Password CLI: {e}")
    if r.returncode != 0:
        sys.exit(f"op read {value} failed: {r.stderr.strip()}")
    return r.stdout


def _to_https(url: str) -> str:
    return url.replace("libsql://", "https://", 1) if url.startswith("libsql://") else url


def execute(sql: str, args: list | None = None) -> list[dict]:
    """POST a single statement to Turso's HTTP pipeline. Returns list of row dicts."""
    url, token = load_creds()
    stmt: dict = {"sql": sql}
    if args:
        stmt["args"] = [_arg(a) for a in args]
    body = json.dumps({
        "requests": [
            {"type": "execute", "stmt": stmt},
            {"type": "close"},
        ]
    }).encode()
    req = urllib.request.Request(
        f"{url}/v2/pipeline",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "lizard-inbox-cli/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:300]}")

    result = payload["results"][0]
    if result.get("type") == "error":
        sys.exit(f"sql error: {result['error']['message']}")
    res = result["response"]["result"]
    cols = [c["name"] for c in res["cols"]]
    return [{c: _val(v) for c, v in zip(cols, row)} for row in res["rows"]]


def _arg(v) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def _val(cell: dict):
    t = cell.get("type")
    if t == "null":
        return None
    v = cell.get("value")
    if t == "integer":
        return int(v)
    if t == "float":
        return float(v)
    return v


# ---- formatting -----------------------------------------------------------

def fmt(rows: list[dict], as_json: bool) -> str:
    if as_json:
        return json.dumps(rows, ensure_ascii=False, indent=2)
    if not rows:
        return "(no rows)"
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r.get(c) or "")) for r in rows)) for c in cols}
    out = ["  ".join(c.ljust(widths[c]) for c in cols),
           "  ".join("-" * widths[c] for c in cols)]
    for r in rows:
        out.append("  ".join(str(r.get(c) or "").ljust(widths[c]) for c in cols))
    return "\n".join(out)


# ---- subcommands ----------------------------------------------------------

PREVIEW_SQL = (
    "SELECT id, message_type, user_display_name, "
    "substr(coalesce(text, sticker_id, file_name, location_title, ''), 1, 80) AS preview, "
    "datetime(line_timestamp_ms/1000, 'unixepoch', '+8 hours') AS at_tw "
    "FROM messages"
)


def cmd_latest(args):
    return execute(f"{PREVIEW_SQL} ORDER BY id DESC LIMIT ?", [args.n])


def cmd_today(_):
    return execute(
        f"{PREVIEW_SQL} "
        "WHERE date(line_timestamp_ms/1000, 'unixepoch', '+8 hours') "
        "    = date('now', '+8 hours') "
        "ORDER BY id DESC"
    )


def cmd_search(args):
    return execute(
        f"{PREVIEW_SQL} WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
        [f"%{args.query}%", args.n],
    )


def cmd_count(_):
    return execute(
        "SELECT message_type, COUNT(*) AS n FROM messages "
        "GROUP BY message_type ORDER BY n DESC"
    )


def cmd_since(args):
    # Relative ("-7 days", "+1 hour") use 'now' as the anchor; absolute
    # ("2026-05-01") parses directly. CAST AS INTEGER is required — strftime
    # returns TEXT and SQLite's INTEGER>=TEXT comparison silently yields NULL.
    if args.when.startswith(("-", "+")):
        anchor = "CAST(strftime('%s', 'now', ?) AS INTEGER)"
    else:
        anchor = "CAST(strftime('%s', ?) AS INTEGER)"
    sql = (
        f"{PREVIEW_SQL} "
        f"WHERE line_timestamp_ms/1000 >= {anchor} "
        "ORDER BY id DESC LIMIT ?"
    )
    return execute(sql, [args.when, args.n])


def cmd_get(args):
    return execute("SELECT * FROM messages WHERE id = ?", [args.id])


def cmd_mentions(args):
    """Synthesized view: only @-mentions of the bot, with the quoted
    original message LEFT-JOINed in so you see what was being replied to.
    quoted_* columns are NULL when the original isn't in our archive."""
    sql = (
        "SELECT m.id, m.source_type, "
        "  m.user_display_name AS by_who, "
        "  substr(coalesce(m.text,''), 1, 60) AS text, "
        "  q.id AS reply_to_id, "
        "  q.user_display_name AS reply_to_who, "
        "  substr(coalesce(q.text,''), 1, 60) AS reply_to_text, "
        "  datetime(m.line_timestamp_ms/1000,'unixepoch','+8 hours') AS at_tw "
        "FROM messages m "
        "LEFT JOIN messages q "
        "  ON m.quoted_message_id = q.message_id "
        "  AND coalesce(m.source_group_id,'') = coalesce(q.source_group_id,'') "
        "WHERE m.is_self_mention = 1 "
        "ORDER BY m.id DESC LIMIT ?"
    )
    return execute(sql, [args.n])


def cmd_attachments(args):
    """Binary-type messages with their R2 archival state.

    --missing filters to rows where r2_key IS NULL — useful for spotting
    download failures still within the 7-day LINE retention window."""
    where = "WHERE message_type IN ('image','video','audio','file')"
    if args.missing:
        where += " AND r2_key IS NULL"
    sql = (
        "SELECT id, message_type, "
        "  coalesce(r2_key, '(missing)') AS r2_key, "
        "  user_display_name, "
        "  substr(coalesce(file_name,''), 1, 30) AS file_name, "
        "  datetime(line_timestamp_ms/1000,'unixepoch','+8 hours') AS at_tw "
        f"FROM messages {where} "
        "ORDER BY id DESC LIMIT ?"
    )
    return execute(sql, [args.n])


def _read_env_key(path: pathlib.Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key and v.strip():
            return v.strip()
    return None


def load_line_token() -> str | None:
    """LINE_CHANNEL_ACCESS_TOKEN, searched in the same spirit as the Turso creds:
    explicit override → skill .env → project .dev.vars → project .env → shell env."""
    candidates: list[pathlib.Path] = []
    override = os.environ.get("LINE_INBOX_ENV")
    if override:
        candidates.append(pathlib.Path(override))
    candidates += [SKILL_ENV, PROJECT_DEV_VARS, PROJECT_ENV]
    for path in candidates:
        tok = _read_env_key(path, "LINE_CHANNEL_ACCESS_TOKEN")
        if tok:
            return _op_resolve(tok)
    return os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or None


LINE_CONTENT_URL = "https://api-data.line.me/v2/bot/message/{}/content"
_EXT_BY_TYPE = {"image": ".jpg", "video": ".mp4", "audio": ".m4a"}
_BINARY_TYPES = ("image", "video", "audio", "file")


def _ext_for(mtype: str, file_name: str | None) -> str:
    if mtype == "file" and file_name and "." in file_name:
        return "." + file_name.rsplit(".", 1)[1].lower()
    return _EXT_BY_TYPE.get(mtype, ".bin")


def _cache_path(row: dict) -> pathlib.Path:
    mid = row["message_id"]
    ext = _ext_for(row["message_type"], row.get("file_name"))
    # Prefer the R2 key's YYYY-MM prefix so local layout mirrors the bucket;
    # fall back to the message's own month when it was never archived.
    month = (row.get("r2_key") or "").split("/")[0] or row.get("ym") or "unknown"
    return MEDIA_ROOT / month / f"{mid}{ext}"


def _fetch_line(mid: str, dest: pathlib.Path, token: str) -> bool:
    """LINE content API — fast, but only within the ~7-day retention window."""
    req = urllib.request.Request(
        LINE_CONTENT_URL.format(mid), headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False  # 404 => outside retention; caller falls back to R2
    if not data:
        return False
    dest.write_bytes(data)
    return True


def _fetch_r2(r2_key: str, dest: pathlib.Path) -> tuple[bool, str]:
    """R2 via wrangler — authoritative, all-time, but needs Cloudflare auth."""
    if not shutil.which("pnpm"):
        return False, "pnpm not found"
    proc = subprocess.run(
        ["pnpm", "dlx", "wrangler", "r2", "object", "get",
         f"lizard-attachments/{r2_key}", "--remote", "--file", str(dest)],
        capture_output=True, text=True,
    )
    if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
        return True, "r2"
    return False, (proc.stderr or proc.stdout or "").strip()[:200]


def _pull_one(row: dict, token: str | None) -> dict:
    """Ensure a local copy of one binary message exists. Never raises —
    returns a status dict so bulk pulls can keep going past failures."""
    mtype = row["message_type"]
    if mtype not in _BINARY_TYPES:
        return {"id": row["id"], "source": "error",
                "error": f"'{mtype}' has no binary content"}
    dest = _cache_path(row)
    if dest.exists() and dest.stat().st_size > 0:
        return {"id": row["id"], "path": str(dest), "source": "cache",
                "bytes": dest.stat().st_size}
    dest.parent.mkdir(parents=True, exist_ok=True)
    mid = row["message_id"]
    if token and _fetch_line(mid, dest, token):
        return {"id": row["id"], "path": str(dest), "source": "line-api",
                "bytes": dest.stat().st_size}
    r2_key = row.get("r2_key")
    if r2_key:
        ok, info = _fetch_r2(r2_key, dest)
        if ok:
            return {"id": row["id"], "path": str(dest), "source": "r2",
                    "bytes": dest.stat().st_size}
        return {"id": row["id"], "source": "error",
                "error": f"LINE unavailable (>7d?) and R2 failed: {info}"}
    return {"id": row["id"], "source": "error",
            "error": "LINE content gone (>7d) and no r2_key to fall back on"}


def cmd_pull(args):
    """Download binary content to the local media cache.

    `pull <id>` grabs one message; `pull --since WHEN` (or bare `pull`) mirrors
    every image/video/audio/file row in the window, skipping ones already cached.
    """
    ym = "strftime('%Y-%m', line_timestamp_ms/1000, 'unixepoch') AS ym"
    if args.id is not None:
        rows = execute(
            f"SELECT id, message_id, message_type, file_name, r2_key, {ym} "
            "FROM messages WHERE id = ?", [args.id])
        if not rows:
            sys.exit(f"no message with id {args.id}")
    else:
        where = f"WHERE message_type IN {_BINARY_TYPES}"
        params: list = []
        if args.since:
            anchor = ("CAST(strftime('%s','now',?) AS INTEGER)"
                      if args.since.startswith(("-", "+"))
                      else "CAST(strftime('%s',?) AS INTEGER)")
            where += f" AND line_timestamp_ms/1000 >= {anchor}"
            params.append(args.since)
        params.append(args.n)
        rows = execute(
            f"SELECT id, message_id, message_type, file_name, r2_key, {ym} "
            f"FROM messages {where} ORDER BY id DESC LIMIT ?", params)
        if not rows:
            return []

    token = load_line_token()
    results = [_pull_one(r, token) for r in rows]

    if args.open:
        for r in results:
            if r.get("path"):
                subprocess.run(["open", r["path"]], check=False)
    return results


def cmd_sql(args):
    return execute(args.sql)


# ---- argparse -------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Query the LINE inbox Turso DB")
    p.add_argument("--json", action="store_true", help="output JSON instead of table")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("latest", help="last N messages (default 10)")
    sp.add_argument("-n", type=int, default=10)
    sp.set_defaults(fn=cmd_latest)

    sp = sub.add_parser("today", help="today's messages (Asia/Taipei)")
    sp.set_defaults(fn=cmd_today)

    sp = sub.add_parser("search", help="LIKE-search the text column")
    sp.add_argument("query")
    sp.add_argument("-n", type=int, default=20)
    sp.set_defaults(fn=cmd_search)

    sp = sub.add_parser("count", help="count rows by message_type")
    sp.set_defaults(fn=cmd_count)

    sp = sub.add_parser("since", help="messages on/after WHEN (e.g. '2026-05-01' or '-7 days')")
    sp.add_argument("when")
    sp.add_argument("-n", type=int, default=50)
    sp.set_defaults(fn=cmd_since)

    sp = sub.add_parser("get", help="full row for one id")
    sp.add_argument("id", type=int)
    sp.set_defaults(fn=cmd_get)

    sp = sub.add_parser(
        "mentions",
        help="@-mention messages with the quoted original LEFT-JOINed in",
    )
    sp.add_argument("-n", type=int, default=20)
    sp.set_defaults(fn=cmd_mentions)

    sp = sub.add_parser(
        "attachments",
        help="image/video/audio/file rows + their R2 keys",
    )
    sp.add_argument("-n", type=int, default=20)
    sp.add_argument(
        "--missing",
        action="store_true",
        help="only show rows where r2_key IS NULL (likely failed downloads)",
    )
    sp.set_defaults(fn=cmd_attachments)

    sp = sub.add_parser(
        "pull",
        help="download binary content to the local media cache (LINE API → R2 fallback)",
    )
    sp.add_argument("id", type=int, nargs="?",
                    help="DB id of one message; omit to bulk-pull a window")
    sp.add_argument("--since",
                    help="bulk mode: only rows on/after WHEN (e.g. '-7 days')")
    sp.add_argument("-n", type=int, default=50,
                    help="bulk mode: cap number of rows (default 50)")
    sp.add_argument("--open", action="store_true",
                    help="open each pulled file with the OS default app")
    sp.set_defaults(fn=cmd_pull)

    sp = sub.add_parser("sql", help="raw SELECT (no params)")
    sp.add_argument("sql")
    sp.set_defaults(fn=cmd_sql)

    args = p.parse_args()
    rows = args.fn(args)
    print(fmt(rows, args.json))


if __name__ == "__main__":
    main()
