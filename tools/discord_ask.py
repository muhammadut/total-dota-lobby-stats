"""
Ask the Discord channel to confirm a player-name merge, and apply the answer.

Merges that clear the automatic bar are applied silently. This handles the
ones that do not: it posts the question, seeds a check and a cross, and
lets an approver vote by reacting. A later run reads the reactions and acts.

    python tools/discord_ask.py --ask --canonical "A" --alias "B" --reason "..."
    python tools/discord_ask.py --resolve        # read votes, apply or drop
    python tools/discord_ask.py --pending        # list open questions

Reactions, not buttons: buttons deliver clicks to a webhook, which needs a
server that is awake when someone clicks. Reactions just sit on the message
until the next scheduled run reads them, which suits a cron with no host.

Open questions live in data/pending_merges.json, which is COMMITTED -- a
cloud runner gets a fresh checkout, so anything not in git did not happen.

Approvers are listed in tools/discord.local.json as "approvers": [ids].
With no list, any non-bot vote counts and the voter is recorded either way.

Needs SEND_MESSAGES and ADD_REACTIONS: re-invite with permissions=68672.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from discord_pull import API, CONF, api, config          # noqa: E402
from ingest import dump_matches, DATA                    # noqa: E402

PENDING = ROOT / "data" / "pending_merges.json"
YES, NO = "✅", "❌"          # white check mark, cross mark


def load_pending():
    return json.loads(PENDING.read_text(encoding="utf-8")) if PENDING.exists() else []


def save_pending(rows):
    PENDING.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")


def approvers():
    """
    Whose vote counts. Env first so a cloud runner, which never sees the
    local config file, can be given the list as a secret:
    DISCORD_APPROVERS="id1,id2". Empty list = anyone's vote counts.
    """
    env = os.environ.get("DISCORD_APPROVERS", "").strip()
    if env:
        return [x.strip() for x in env.split(",") if x.strip()]
    if CONF.exists():
        return [str(x) for x in json.loads(CONF.read_text(encoding="utf-8"))
                .get("approvers", [])]
    return []


def post(token, channel, text):
    # allow_mentions.parse=["users"] lets <@id> in bot messages actually
    # notify the user (required for the league readiness pings). Roles and
    # @everyone are still blocked -- the bot must never mass-ping.
    req = urllib.request.Request(
        f"{API}/channels/{channel}/messages",
        data=json.dumps({"content": text,
                         "allowed_mentions": {"parse": ["users"]}}).encode("utf-8"),
        headers={"Authorization": "Bot " + token,
                 "Content-Type": "application/json",
                 "User-Agent": "DotaLobbyStats/1.0"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def react(token, channel, msg_id, emoji, tries=4):
    """
    Seed one reaction. Discord rate-limits reaction writes aggressively --
    adding two in a row reliably 429s -- and the response carries the exact
    wait in retry_after, so honour it rather than guessing at a sleep.
    """
    e = urllib.parse.quote(emoji)
    for attempt in range(tries):
        req = urllib.request.Request(
            f"{API}/channels/{channel}/messages/{msg_id}/reactions/{e}/@me",
            headers={"Authorization": "Bot " + token,
                     "User-Agent": "DotaLobbyStats/1.0"},
            method="PUT")
        try:
            urllib.request.urlopen(req, timeout=30).read()
            return
        except urllib.error.HTTPError as ex:
            if ex.code != 429 or attempt == tries - 1:
                raise
            try:
                wait = float(json.loads(ex.read()).get("retry_after", 1.0))
            except Exception:
                wait = 1.0
            time.sleep(min(wait + 0.25, 10))


def voters(token, channel, msg_id, emoji):
    e = urllib.parse.quote(emoji)
    try:
        users = api(f"/channels/{channel}/messages/{msg_id}/reactions/{e}?limit=50", token)
    except SystemExit:
        return []
    return [(u["id"], u.get("username", "?")) for u in users if not u.get("bot")]


def cmd_ask(token, channel, args) -> int:
    rows = load_pending()
    if any(r["canonical"] == args.canonical and r["alias"] == args.alias for r in rows):
        print("  already asked; not posting again.")
        return 0

    text = (f"**Same player?**\n"
            f"`{args.alias}`  →  `{args.canonical}`\n"
            f"_{args.reason}_\n"
            f"They have never appeared in the same match, so it is possible.\n\n"
            f"**Reply `yes` or `no`** to this message. "
            f"(Or say `not same {args.alias} and {args.canonical}` any time.)")
    msg = post(token, channel, text)
    # Reactions are no longer seeded -- words are the interface now. They are
    # still READ on resolve, since people react instinctively and it costs
    # nothing to honour that.
    rows.append({"message_id": msg["id"], "canonical": args.canonical,
                 "alias": args.alias, "reason": args.reason})
    save_pending(rows)
    print(f"  asked: {args.alias!r} -> {args.canonical!r}  (message {msg['id']})")
    return 0


def cmd_resolve(token, channel) -> int:
    rows = load_pending()
    if not rows:
        print("  no open questions.")
        return 0

    allow = approvers()
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    known = {(a["canonical"], a["alias"]) for a in payload["aliases"]}
    still, applied, rejected = [], [], []

    for r in rows:
        yes = voters(token, channel, r["message_id"], YES)
        no = voters(token, channel, r["message_id"], NO)
        if allow:
            yes = [v for v in yes if v[0] in allow]
            no = [v for v in no if v[0] in allow]

        # A single no outweighs any number of yes. A wrong merge silently
        # corrupts two win rates; a missed one is asked again tomorrow.
        if no:
            rejected.append((r, no))
        elif yes:
            if (r["canonical"], r["alias"]) not in known:
                payload["aliases"].append({
                    "canonical": r["canonical"], "alias": r["alias"],
                    "note": f"confirmed in Discord by {', '.join(n for _, n in yes)}"})
            applied.append((r, yes))
        else:
            still.append(r)

    for r, who in applied:
        print(f"  MERGED  {r['alias']!r} -> {r['canonical']!r}"
              f"   (yes: {', '.join(n for _, n in who)})")
    for r, who in rejected:
        print(f"  KEPT SEPARATE  {r['alias']!r} / {r['canonical']!r}"
              f"   (no: {', '.join(n for _, n in who)})")
    if still:
        print(f"  still waiting on {len(still)} question(s).")

    if applied:
        DATA.write_text(dump_matches(payload), encoding="utf-8")
        json.loads(DATA.read_text(encoding="utf-8"))
        print(f"\n  wrote {len(applied)} alias(es). Re-run load.py and export_web.py.")
    save_pending(still)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ask", action="store_true")
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--canonical")
    ap.add_argument("--alias")
    ap.add_argument("--reason", default="similar names, never in the same match")
    args = ap.parse_args()

    if args.pending:
        for r in load_pending():
            print(f"  {r['alias']!r} -> {r['canonical']!r}  (message {r['message_id']})")
        return 0

    token, channel = config()
    if args.ask:
        if not args.canonical or not args.alias:
            sys.exit("--ask needs --canonical and --alias")
        return cmd_ask(token, channel, args)
    if args.resolve:
        return cmd_resolve(token, channel)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
