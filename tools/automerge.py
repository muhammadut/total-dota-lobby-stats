"""
Auto-merge player identities that are near-certainly the same person, and
report the weaker candidates for a human to decide.

A wrong merge is SILENT: two people become one row and both win rates are
wrong with nothing to flag it. So the bar for acting without asking is set
deliberately high, and every auto-merge is written into the `aliases` array
of data/matches.json — durable, reversible, and visible in the diff — never
applied straight to the database.

    python tools/automerge.py            # apply auto-merges, list the rest
    python tools/automerge.py --dry-run  # show what would happen

Hard precondition for ANY merge: the two names never appear in the same
match. One person cannot hold two slots in one game, so a co-occurrence is
proof they are different people regardless of how alike the names look.
"""

import argparse
import difflib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ingest import dump_matches      # noqa: E402  -- one JSON writer, not two

DB = ROOT / "dota_stats.db"
DATA = ROOT / "data" / "matches.json"

MIN_STEM = 4          # shorter stem must be at least this long to act on
FUZZY = 0.85          # similarity needed when no containment applies


def clan(name: str):
    m = re.search(r"\[([^\[\]]+)\]\s*$", name)
    return m.group(1).strip().lower() if m else None


def norm(name: str) -> str:
    """Strip the trailing clan tag, then all punctuation, spacing and case.
    '____Tiger X____ [GB]' and 'TigerX [GB]' both reduce to 'tigerx'."""
    base = re.sub(r"\[[^\[\]]*\]\s*$", "", name)
    return re.sub(r"[^a-z0-9]+", "", base.lower())


def classify(a: str, b: str):
    """Return (verdict, rule) where verdict is 'auto', 'ask' or None."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return None, None
    short, long = sorted((na, nb), key=len)
    if len(short) < MIN_STEM:
        return None, None
    same_clan = clan(a) and clan(a) == clan(b)
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()

    if na == nb:
        return "auto", "identical once case, punctuation and clan tag are stripped"
    if long.startswith(short):
        return "auto", f"'{short}' is a prefix of '{long}'"
    if same_clan and short in long:
        return "auto", f"'{short}' is contained in '{long}', same clan tag"
    if ratio >= FUZZY:
        return "auto", f"{ratio:.0%} similar"
    if same_clan and ratio >= 0.30:
        return "ask", f"same clan tag, only {ratio:.0%} similar"
    if ratio >= 0.70:
        return "ask", f"{ratio:.0%} similar"
    return None, None


def pick_canonical(a, b, games):
    """Prefer the name with more games, then the least decorated, then the
    shortest -- 'TigerX [GB]' over '____Tiger X____ [GB]'."""
    def key(n):
        deco = sum(1 for c in n if not c.isalnum() and c not in " []")
        return (-games.get(n, 0), deco, len(n))
    return (a, b) if key(a) <= key(b) else (b, a)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ask-discord", action="store_true",
                    help="post the too-weak candidates to Discord for a vote")
    args = ap.parse_args()

    if not DB.exists():
        sys.exit("dota_stats.db not found -- run `python load.py` first.")
    con = sqlite3.connect(DB)
    cur = con.cursor()

    players = cur.execute("SELECT id, display_name FROM players").fetchall()
    games = dict(cur.execute("""
        SELECT p.display_name, COUNT(*) FROM match_players mp
        JOIN players p ON p.id = mp.player_id GROUP BY p.display_name""").fetchall())

    together = set()
    for a, b in cur.execute("""
        SELECT x.player_id, y.player_id FROM match_players x
        JOIN match_players y ON x.match_id = y.match_id AND x.player_id < y.player_id"""):
        together.add((a, b))

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    aliases = payload.setdefault("aliases", [])
    known = {(x["canonical"], x["alias"]) for x in aliases}
    known |= {(x["alias"], x["canonical"]) for x in aliases}
    already_alias = {x["alias"] for x in aliases}

    auto, ask = [], []
    for i, (ida, a) in enumerate(players):
        for idb, b in players[i + 1:]:
            if (min(ida, idb), max(ida, idb)) in together:
                continue                      # proof they are different people
            if (a, b) in known:
                continue
            # Merging into or out of an existing alias would build a chain,
            # which the single-level resolution in v_player cannot follow.
            if a in already_alias or b in already_alias:
                continue
            verdict, rule = classify(a, b)
            if verdict == "auto":
                canon, alias = pick_canonical(a, b, games)
                auto.append((canon, alias, rule))
            elif verdict == "ask":
                ask.append((a, b, rule))

    print()
    if auto:
        print(f"  AUTO-MERGED {len(auto)}:")
        for canon, alias, rule in auto:
            print(f"    {alias!r}  ->  {canon!r}")
            print(f"      because {rule}")
            aliases.append({"canonical": canon, "alias": alias,
                            "note": f"auto-merged: {rule}"})
    else:
        print("  AUTO-MERGED 0 — no name pairs met the bar.")

    if ask:
        print(f"\n  NEEDS YOUR CALL {len(ask)} (too weak to merge unasked):")
        for a, b, rule in ask:
            print(f"    {a!r}  ~  {b!r}   ({rule})")

    # Anything below the automatic bar becomes a question in the channel
    # rather than a line in a log nobody reads. discord_ask.py refuses to
    # re-post a pair that is already open, so this is safe to run daily.
    if ask and args.ask_discord and not args.dry_run:
        print()
        for a, b, rule in ask:
            canon, alias = pick_canonical(a, b, games)
            subprocess.run([sys.executable, str(ROOT / "tools" / "discord_ask.py"),
                            "--ask", "--canonical", canon, "--alias", alias,
                            "--reason", rule], cwd=ROOT)

    if args.dry_run:
        print("\n  dry run — nothing written.")
        return 0
    if not auto:
        return 0

    DATA.write_text(dump_matches(payload), encoding="utf-8")
    json.loads(DATA.read_text(encoding="utf-8"))
    print(f"\n  wrote {len(auto)} alias(es) to {DATA.relative_to(ROOT)}")
    for cmd in (["load.py"], ["export_web.py"]):
        r = subprocess.run([sys.executable] + cmd, cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        for line in (r.stdout or "").splitlines():
            print("    " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
