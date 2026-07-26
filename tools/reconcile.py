"""
Account for every screenshot that was downloaded, and tell the channel
about the ones that did not make it in.

The gap this closes: images are pulled, the model ingests some and refuses
others, the runner is destroyed, and inbox/ is gitignored. Without this,
a refused screenshot is simply gone — no record, no retry, no message,
and a green checkmark. One lost match is one wrong win total at year end.

Every downloaded image ends in exactly one state:
  ingested  a match in data/matches.json carries its message id
  failed    it does not, and the channel is told to repost it

Nothing is retried silently. A human is asked instead, because a
screenshot that failed once will almost always fail again for the same
reason, and re-reading it daily just burns money.

    python tools/reconcile.py            # settle pending, notify failures
    python tools/reconcile.py --dry-run
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from discord_pull import SEEN, load_seen, save_seen        # noqa: E402

DATA = ROOT / "data" / "matches.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    seen = load_seen()
    pending = {k: v for k, v in seen.items() if v.get("status") == "pending"}
    if not pending:
        print("  nothing pending — every downloaded screenshot is accounted for.")
        return 0

    # A Discord-sourced match is recorded as source_ref "discord-<message id>",
    # so presence in the ledger is the proof that it landed.
    refs = {m.get("source_ref", "") for m in
            json.loads(DATA.read_text(encoding="utf-8"))["matches"]}
    got = {r[len("discord-"):] for r in refs if r.startswith("discord-")}

    ingested, failed = [], []
    for mid, info in pending.items():
        (ingested if mid in got else failed).append((mid, info))

    for mid, info in ingested:
        print(f"  ingested  {info.get('file', mid)}")
        seen[mid]["status"] = "ingested"

    for mid, info in failed:
        who = info.get("author", "someone")
        print(f"  FAILED    {info.get('file', mid)}  (posted by {who})")
        seen[mid]["status"] = "failed"
        if args.dry_run:
            continue
        # Ask for a repost rather than retrying: the same image will fail
        # the same way tomorrow, and silence is what loses the match.
        subprocess.run(
            [sys.executable, str(ROOT / "tools" / "discord_notify.py"),
             "--reply-to", mid,
             "I couldn't read that one well enough to record it. Please repost the "
             "post-game **SCOREBOARD** tab — both teams, five players each, and both "
             "scores visible. It isn't in the standings until then."],
            cwd=ROOT)

    if not args.dry_run:
        save_seen(seen)

    print(f"\n  {len(ingested)} ingested, {len(failed)} failed"
          + (" (dry run — nothing written)" if args.dry_run else ""))
    # Loud on failure: this is the one condition that means screenshots
    # arrived and were not recorded, and it must not look like a quiet day.
    return 1 if failed and not args.dry_run else 0


if __name__ == "__main__":
    sys.exit(main())
