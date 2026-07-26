"""
Post a short message back to the Discord channel.

Closes the loop on unattended runs: if a screenshot is rejected, the person
who posted it finds out in the channel rather than the match silently never
appearing. Silence is the worst failure mode for automation nobody watches.

    python tools/discord_notify.py "text"
    python tools/discord_notify.py --reply-to <message_id> "text"
    python tools/discord_notify.py --dry-run "text"

Needs SEND_MESSAGES on top of the read permissions. If the bot was invited
with read-only access, re-invite it with permissions=68608 (View Channel +
Read Message History + Send Messages) -- the existing invite URL works, just
change the number.

Exits 0 even when it cannot post. A failed notification must never fail the
run that was otherwise successful.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from discord_pull import API, config          # noqa: E402 -- one config loader


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("--reply-to", help="message id to reply to, threading the answer")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Discord hard-caps a message at 2000 characters.
    text = args.text[:1900]
    if args.dry_run:
        print("  would post:\n    " + text.replace("\n", "\n    "))
        return 0

    token, channel = config()
    payload = {"content": text, "allowed_mentions": {"parse": []}}
    if args.reply_to:
        # fail_if_not_exists=False so a deleted message downgrades to a plain
        # post instead of erroring.
        payload["message_reference"] = {"message_id": args.reply_to,
                                        "fail_if_not_exists": False}

    req = urllib.request.Request(
        f"{API}/channels/{channel}/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bot " + token,
                 "Content-Type": "application/json",
                 "User-Agent": "DotaLobbyStats/1.0"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"  posted (message {json.loads(r.read())['id']})")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        if e.code == 403:
            print("  ! cannot post: the bot lacks SEND_MESSAGES in this channel.\n"
                  "    Re-invite it with permissions=68608.", file=sys.stderr)
        else:
            print(f"  ! notify failed ({e.code}): {detail}", file=sys.stderr)
        return 0        # never fail the run over a notification
    except Exception as e:
        print(f"  ! notify failed: {e}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
