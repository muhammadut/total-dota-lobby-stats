"""
Account for every screenshot that was downloaded, and tell the channel
about the ones that did not make it in.

The gap this closes: images are pulled, the model ingests some and refuses
others, the runner is destroyed, and inbox/ is gitignored. Without this,
a refused screenshot is simply gone — no record, no retry, no message,
and a green checkmark. One lost match is one wrong win total at year end.

Every downloaded image ends in exactly one state:
  ingested   a match in data/matches.json carries its message id
  duplicate  a re-capture of a game already recorded from another image
  failed     none of the above, and the channel is told to repost it

Nothing is retried silently. A human is asked instead, because a
screenshot that failed once will almost always fail again for the same
reason, and re-reading it daily just burns money.

`duplicate` exists because it was missing. Two people photograph the same
post-game screen; the first is ingested, and the second is perfectly
readable but must not be recorded twice, so it is not in the ledger --
which looked identical to "unreadable". The bot told soooze his screenshot
could not be read and asked him to repost it. It could be read. Telling a
real person to redo work they did correctly is its own kind of wrong
number, so a duplicate is now a state of its own: terminal, and silent.

It cannot be detected automatically -- knowing two images are the same
match means transcribing both -- so whoever ingests marks it:

    python tools/reconcile.py            # settle pending, notify failures
    python tools/reconcile.py --dry-run
    python tools/reconcile.py --duplicate <message_id> --of <source_ref>
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
# discord_pull now serves two channels, so its seen-ledger is per-source.
# reconcile accounts for the LOBBY inbox only -- league screenshots live in
# inbox_league/ with their own ledger, and counting them here would report
# every one of them as a failed lobby ingest forever.
from discord_pull import SOURCES, load_seen, save_seen      # noqa: E402

SEEN = SOURCES["lobby"]["seen"]

DATA = ROOT / "data" / "matches.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--duplicate", metavar="MESSAGE_ID",
                    help="mark a downloaded image as a re-capture of a match "
                         "already recorded from a different image")
    ap.add_argument("--of", metavar="SOURCE_REF",
                    help="the source_ref it duplicates (required with --duplicate)")
    args = ap.parse_args()

    seen = load_seen(SEEN)

    if args.duplicate:
        if not args.of:
            return ap.error("--duplicate needs --of <source_ref>")
        mid = args.duplicate
        if mid not in seen:
            print(f"  no downloaded image with message id {mid}", file=sys.stderr)
            return 1
        refs = {m.get("source_ref", "") for m in
                json.loads(DATA.read_text(encoding="utf-8"))["matches"]}
        if args.of not in refs:
            # Refuse to point at a match that does not exist -- otherwise
            # this becomes a way to silence a real failure by typo.
            print(f"  {args.of!r} is not a source_ref in the ledger", file=sys.stderr)
            return 1
        seen[mid]["status"] = "duplicate"
        seen[mid]["duplicate_of"] = args.of
        if not args.dry_run:
            save_seen(SEEN, seen)
        print(f"  {seen[mid].get('file', mid)} → duplicate of {args.of}"
              + (" (dry run — nothing written)" if args.dry_run else ""))
        return 0
    pending = {k: v for k, v in seen.items() if v.get("status") == "pending"}
    # "failed" used to be terminal. But a screenshot is marked failed the
    # moment it is not yet in the ledger, and that is also true of one being
    # ingested a minute later -- run this before the ingest rather than after
    # and a perfectly good screenshot is condemned permanently, with the
    # poster told to repost it. Re-examine failures too, so the record can
    # heal. Promotion only: a failure that is still absent is left alone and
    # is never re-notified.
    stale = {k: v for k, v in seen.items() if v.get("status") == "failed"}
    if not pending and not stale:
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

    # A past failure that has since landed is a correction, not news.
    healed = [(mid, info) for mid, info in stale.items() if mid in got]
    for mid, info in healed:
        print(f"  CORRECTED {info.get('file', mid)} — was marked failed, "
              f"but it is in the ledger now.")
        seen[mid]["status"] = "ingested"

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
        save_seen(SEEN, seen)

    print(f"\n  {len(ingested)} ingested, {len(failed)} failed"
          + (f", {len(healed)} corrected" if healed else "")
          + (" (dry run — nothing written)" if args.dry_run else ""))
    # Loud on failure: this is the one condition that means screenshots
    # arrived and were not recorded, and it must not look like a quiet day.
    return 1 if failed and not args.dry_run else 0


if __name__ == "__main__":
    sys.exit(main())
