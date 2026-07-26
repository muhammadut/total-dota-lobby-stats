"""
Read plain-English instructions from the Discord channel and act on them.

Reactions worked but are a poor interface: you cannot say *which* two names
you mean, and you cannot ask a question. This reads actual messages.

    python tools/discord_commands.py            # process new commands
    python tools/discord_commands.py --dry-run  # parse and reply-preview only

Understood in the channel (case-insensitive, approvers only for anything
that writes):

    yes / no                    answer the merge question you replied to
    merge A and B               A and B are the same player
    not same A and B            they are different -- never ask again
    who is A                    what the database knows about a name
    status                      current top of the standings
    help                        list these

Names are matched loosely against known players, so `merge sesto and
hypermodel` resolves to `sesto [M.C]` and `HypermodeL [M.C]`. If a name is
ambiguous the bot says so and does nothing rather than guessing.

Its own watermark, separate from the screenshot one, so a command is never
consumed by an image pull or vice versa.
"""

import argparse
import difflib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from discord_pull import api, config                       # noqa: E402
from discord_ask import (approvers, load_pending, save_pending,  # noqa: E402
                         post as post_msg)
from ingest import dump_matches, DATA                      # noqa: E402

MARK = ROOT / "data" / "discord_cmd_watermark.txt"
REJECTED = ROOT / "data" / "rejected_merges.json"
DB = ROOT / "dota_stats.db"

HELP = (
    "**What I understand**\n"
    "`merge A and B` — same player, e.g. `merge sesto and hypermodel`\n"
    "`not same A and B` — different people, I'll stop asking\n"
    "`yes` / `no` — reply to one of my questions to answer it\n"
    "`who is A` — what I have on a name\n"
    "`status` — current standings\n"
    "`help` — this\n\n"
    "For matches: post the **post-game SCOREBOARD tab** — both teams, five "
    "players each, both scores visible. I read it automatically."
)


def players():
    if not DB.exists():
        return []
    con = sqlite3.connect(DB)
    return [r[0] for r in con.execute("SELECT display_name FROM players")]


def resolve(typed, names):
    """
    Map loosely-typed text onto a real player name. Exact, then
    case-insensitive, then a stripped-punctuation match, then fuzzy.
    Returns (name, None) or (None, "why it failed").
    """
    t = typed.strip().strip('"\'`')
    if not t:
        return None, "empty name"
    if t in names:
        return t, None
    low = {n.lower(): n for n in names}
    if t.lower() in low:
        return low[t.lower()], None

    def bare(s):
        s = re.sub(r"\[[^\[\]]*\]\s*$", "", s)
        return re.sub(r"[^a-z0-9]+", "", s.lower())
    bt = bare(t)
    hits = [n for n in names if bare(n) == bt]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, f"`{t}` matches {len(hits)} players: " + ", ".join(f"`{h}`" for h in hits)
    hits = [n for n in names if bt and bt in bare(n)]
    if len(hits) == 1:
        return hits[0], None
    close = difflib.get_close_matches(t.lower(), [n.lower() for n in names], n=3, cutoff=0.7)
    if len(close) == 1:
        return low[close[0]], None
    if close:
        return None, f"`{t}` could be " + ", ".join(f"`{low[c]}`" for c in close) + " — be specific"
    return None, f"I don't have a player called `{t}`"


def load_rejected():
    return json.loads(REJECTED.read_text(encoding="utf-8")) if REJECTED.exists() else []


def save_rejected(rows):
    REJECTED.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def co_occur(a, b):
    """True if the two names ever played in the same match -- proof of two people."""
    if not DB.exists():
        return False
    con = sqlite3.connect(DB)
    n = con.execute("""
        SELECT COUNT(*) FROM match_players x
        JOIN match_players y ON x.match_id = y.match_id
        JOIN players pa ON pa.id = x.player_id
        JOIN players pb ON pb.id = y.player_id
        WHERE pa.display_name = ? AND pb.display_name = ?""", (a, b)).fetchone()[0]
    return n > 0


def add_alias(canonical, alias, note):
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    pairs = {(x["canonical"], x["alias"]) for x in payload["aliases"]}
    if (canonical, alias) in pairs:
        return False
    payload["aliases"].append({"canonical": canonical, "alias": alias, "note": note})
    DATA.write_text(dump_matches(payload), encoding="utf-8")
    json.loads(DATA.read_text(encoding="utf-8"))
    return True


def games_played(name):
    if not DB.exists():
        return 0
    con = sqlite3.connect(DB)
    return con.execute("""
        SELECT COUNT(*) FROM match_players mp JOIN players p ON p.id = mp.player_id
        WHERE p.display_name = ?""", (name,)).fetchone()[0]


# ── command handlers ────────────────────────────────────────────────
def do_merge(args, names, author, same=True):
    m = re.split(r"\s+and\s+|\s*,\s*|\s+&\s+", args, maxsplit=1)
    if len(m) != 2:
        return "Say it as `merge A and B` — I need two names separated by `and`."
    a, ea = resolve(m[0], names)
    b, eb = resolve(m[1], names)
    if ea:
        return ea
    if eb:
        return eb
    if a == b:
        return f"`{a}` and `{b}` are already the same row."

    if not same:
        rows = load_rejected()
        if any(set(r) == {a, b} for r in rows):
            return f"Already noted: `{a}` and `{b}` are different people."
        rows.append([a, b])
        save_rejected(rows)
        # Drop any open question about this pair.
        save_pending([p for p in load_pending()
                      if {p["canonical"], p["alias"]} != {a, b}])
        return f"Noted — `{a}` and `{b}` are different people. I won't ask again."

    if co_occur(a, b):
        return (f"I can't merge those: `{a}` and `{b}` have played in the **same match**, "
                f"so they're two different people.")
    canonical, alias = (a, b) if games_played(a) >= games_played(b) else (b, a)
    if add_alias(canonical, alias, f"confirmed in Discord by {author}"):
        save_pending([p for p in load_pending()
                      if {p["canonical"], p["alias"]} != {a, b}])
        return (f"Merged `{alias}` into `{canonical}`. Their games are now counted "
                f"together — the site updates on the next run.")
    return f"`{alias}` was already merged into `{canonical}`."


def do_who(args, names):
    n, err = resolve(args, names)
    if err:
        return err
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    merged = [a for a in payload["aliases"] if a["alias"] == n]
    aka = [a["alias"] for a in payload["aliases"] if a["canonical"] == n]
    out = f"`{n}` — {games_played(n)} game(s) recorded."
    if merged:
        out += f"\nCounted under `{merged[0]['canonical']}`."
    if aka:
        out += "\nAlso known as: " + ", ".join(f"`{x}`" for x in aka)
    return out


def do_status():
    if not DB.exists():
        return "No database yet."
    con = sqlite3.connect(DB)
    rows = con.execute("""
        SELECT player_name, games, wins, losses, win_pct FROM v_player_record
        ORDER BY games DESC, win_pct DESC LIMIT 6""").fetchall()
    n = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    body = "\n".join(f"`{r[0][:24]:<24}` {r[1]:>2} GP  {r[2]}-{r[3]}  {r[4]:.0f}%" for r in rows)
    return f"**{n} matches recorded — most played**\n{body}\nhttps://muhammadut.github.io/total-dota-lobby-stats/"


def do_vote(word, msg, author):
    ref = (msg.get("message_reference") or {}).get("message_id")
    pend = load_pending()
    if not pend:
        return None                      # nothing open; stay quiet
    match = next((p for p in pend if p["message_id"] == ref), None)
    if not match:
        if len(pend) == 1:
            match = pend[0]              # only one open question, no ambiguity
        else:
            return (f"Which one? There are {len(pend)} open questions — "
                    f"reply directly to the one you mean.")
    a, b = match["canonical"], match["alias"]
    if word == "yes":
        if co_occur(a, b):
            save_pending([p for p in pend if p is not match])
            return f"Actually `{a}` and `{b}` played in the same match, so I can't merge them."
        add_alias(a, b, f"confirmed in Discord by {author}")
        save_pending([p for p in pend if p is not match])
        return f"Merged `{b}` into `{a}`. Thanks {author}."
    rows = load_rejected()
    rows.append([a, b])
    save_rejected(rows)
    save_pending([p for p in pend if p is not match])
    return f"Kept `{a}` and `{b}` separate. I won't ask again."


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true", help="ignore the watermark")
    args = ap.parse_args()

    token, channel = config()
    allow = approvers()
    names = players()

    after = None if args.all else (MARK.read_text(encoding="utf-8").strip()
                                   if MARK.exists() else None)
    q = f"/channels/{channel}/messages?limit=50"
    if after:
        q += "&after=" + after
    msgs = sorted(api(q, token), key=lambda m: int(m["id"]))

    newest, acted = None, 0
    for m in msgs:
        newest = m["id"]
        author = m.get("author") or {}
        if author.get("bot"):
            continue
        text = (m.get("content") or "").strip()
        if not text:
            continue
        low = text.lower()
        uid, uname = author.get("id"), author.get("username", "?")
        privileged = (not allow) or (uid in allow)

        reply = None
        if low in ("help", "!help", "commands"):
            reply = HELP
        elif low in ("status", "!status", "standings"):
            reply = do_status()
        elif low.startswith("who is "):
            reply = do_who(text[7:], names)
        elif re.match(r"^(not\s+same|different|separate|split)\b", low):
            if not privileged:
                reply = "Only the lobby admins can change player identities."
            else:
                reply = do_merge(re.sub(r"^(not\s+same|different|separate|split)\s*",
                                        "", text, flags=re.I), names, uname, same=False)
        elif low.startswith("merge "):
            reply = ("Only the lobby admins can change player identities."
                     if not privileged else do_merge(text[6:], names, uname))
        elif low in ("yes", "y", "yep", "confirm", "no", "n", "nope"):
            if not privileged:
                continue                 # ignore votes from non-approvers, silently
            reply = do_vote("yes" if low[0] == "y" or low == "confirm" else "no", m, uname)

        if reply:
            acted += 1
            if args.dry_run:
                print(f"  [{uname}] {text!r}\n    -> {reply.splitlines()[0][:100]}")
            else:
                post_msg(token, channel, reply)
                print(f"  [{uname}] {text!r} -> replied")

    if newest and not args.dry_run:
        MARK.write_text(newest, encoding="utf-8")
    print(f"\n  {acted} command(s) handled"
          + (f", watermark {newest}" if newest and not args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
