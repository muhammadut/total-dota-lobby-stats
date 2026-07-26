"""
Download new image attachments from a Discord channel into inbox/.

Fetch only. Parsing a scoreboard is a judgement task and stays with the
model; this just gets the files onto disk so the add-match skill can read
them. Uses the plain REST API over urllib -- no discord.py, no gateway
socket, no dependencies.

    python tools/discord_pull.py                  # pull anything new
    python tools/discord_pull.py --limit 5        # only the newest few
    python tools/discord_pull.py --since <msg_id> # ignore the watermark
    python tools/discord_pull.py --list           # show, don't download

Setup, once:
  1. https://discord.com/developers/applications -> New Application -> Bot
  2. Under Bot, enable the MESSAGE CONTENT INTENT, and copy the token.
  3. OAuth2 -> URL Generator -> scope `bot`, permissions
     `View Channel` + `Read Message History` -> open the URL, add it to
     the server.
  4. Right-click the channel -> Copy Channel ID (needs Developer Mode on).

Then put both in tools/discord.local.json, which is gitignored:

    {"token": "...", "channel_id": "..."}

or export DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID.

SECURITY: this repository is PUBLIC. A leaked bot token lets anyone drive
the bot, so it must never be committed. The config file and inbox/ are
both in .gitignore; if a token is ever pushed, regenerate it in the
developer portal immediately -- rotating is the only real fix.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "tools" / "discord.local.json"
INBOX = ROOT / "inbox"
MARK = INBOX / ".last_message_id"
API = "https://discord.com/api/v10"
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")


def config():
    tok = os.environ.get("DISCORD_BOT_TOKEN")
    chan = os.environ.get("DISCORD_CHANNEL_ID")
    if CONF.exists():
        c = json.loads(CONF.read_text(encoding="utf-8"))
        tok = tok or c.get("token")
        chan = chan or c.get("channel_id")
    if not tok or not chan:
        sys.exit(
            "No credentials. Create tools/discord.local.json:\n"
            '    {"token": "...", "channel_id": "..."}\n'
            "or set DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID.\n"
            "See the docstring at the top of this file for how to get them.")
    return tok, str(chan)


def api(path, token):
    req = urllib.request.Request(
        API + path,
        headers={"Authorization": "Bot " + token,
                 "User-Agent": "DotaLobbyStats (https://github.com/muhammadut/total-dota-lobby-stats, 1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        if e.code == 401:
            sys.exit("  401 Unauthorized — the bot token is wrong or was regenerated.")
        if e.code == 403:
            sys.exit("  403 Forbidden — the bot is in the server but cannot read this\n"
                     "  channel. Give it View Channel + Read Message History.")
        if e.code == 404:
            sys.exit("  404 Not Found — wrong channel id, or the bot was never added\n"
                     "  to that server.")
        if e.code == 429:
            sys.exit(f"  429 Rate limited. Wait and retry. {body}")
        sys.exit(f"  HTTP {e.code}: {body}")


def check(token, channel) -> int:
    """
    Verify each part of the setup separately, so a failure names the step
    that is wrong instead of just refusing. The four things that break are
    the token, whether the bot is in the server, whether it can see the
    channel, and whether it can read history -- and they fail in that order.
    """
    print("\n  1. token")
    me = api("/users/@me", token)
    print(f"     ok — authenticated as {me.get('username')}#{me.get('discriminator','0')}"
          f" (bot={me.get('bot', False)})")

    print("  2. channel visibility")
    ch = api(f"/channels/{channel}", token)
    kind = {0: "text", 5: "announcement", 11: "public thread",
            12: "private thread"}.get(ch.get("type"), f"type {ch.get('type')}")
    print(f"     ok — #{ch.get('name')}  ({kind})")

    print("  3. read message history")
    msgs = api(f"/channels/{channel}/messages?limit=1", token)
    print(f"     ok — history readable ({len(msgs)} recent message(s) visible)")

    imgs = 0
    for m in api(f"/channels/{channel}/messages?limit=50", token):
        for a in m.get("attachments", []):
            ct = (a.get("content_type") or "").split(";")[0]
            if ct in IMAGE_TYPES:
                imgs += 1
    print(f"  4. images in the last 50 messages: {imgs}")
    print("\n  All good. Run:  python tools/discord_pull.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="verify token, channel access and read permission, step by step")
    ap.add_argument("--limit", type=int, default=50, help="max messages to scan (1-100)")
    ap.add_argument("--since", help="message id to read after, overriding the watermark")
    ap.add_argument("--list", action="store_true", help="show what's new, download nothing")
    ap.add_argument("--all", action="store_true", help="ignore the watermark entirely")
    args = ap.parse_args()

    token, channel = config()
    if args.check:
        return check(token, channel)

    INBOX.mkdir(exist_ok=True)

    after = args.since
    if not after and not args.all and MARK.exists():
        after = MARK.read_text(encoding="utf-8").strip() or None

    q = f"/channels/{channel}/messages?limit={max(1, min(100, args.limit))}"
    if after:
        q += "&after=" + after
    msgs = api(q, token)

    # Discord returns newest-first; walk oldest-first so the watermark only
    # ever moves forward and a mid-run failure can be resumed.
    msgs.sort(key=lambda m: int(m["id"]))

    found = []
    for m in msgs:
        for a in m.get("attachments", []):
            ct = (a.get("content_type") or "").split(";")[0]
            if ct in IMAGE_TYPES or a.get("filename", "").lower().endswith(
                    (".png", ".jpg", ".jpeg", ".webp")):
                found.append((m, a))

    if not found:
        print(f"\n  nothing new in the channel"
              + (f" after message {after}" if after else "") + ".")
        return 0

    print(f"\n  {len(found)} new image(s):")
    newest = None
    for m, a in found:
        who = (m.get("author") or {}).get("username", "?")
        when = (m.get("timestamp") or "")[:16].replace("T", " ")
        kb = (a.get("size") or 0) / 1024
        # Prefix with the message id: unique, and sorts chronologically.
        name = f"{m['id']}_{a['filename']}".replace("/", "_")
        dest = INBOX / name
        print(f"    {when}  {who:<16} {a['filename']}  ({kb:.0f} KB)")
        if args.list:
            continue
        if not dest.exists():
            try:
                req = urllib.request.Request(
                    a["url"], headers={"User-Agent": "DotaLobbyStats/1.0"})
                with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
                    f.write(r.read())
            except Exception as e:
                print(f"      ! download failed: {e}")
                continue
        print(f"      -> {dest.relative_to(ROOT)}")
        newest = m["id"]

    if newest and not args.list:
        MARK.write_text(newest, encoding="utf-8")
        print(f"\n  watermark now {newest}")
    print("\n  Next: ask Claude to \"add the screenshots in inbox/\".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
