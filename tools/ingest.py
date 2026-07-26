"""
Ingest one or more parsed matches, validate them, and republish the site.

This is the deterministic half of the pipeline. Reading a screenshot is a
judgement task and stays with the model; everything after that — checking
the arithmetic, refusing duplicates, rebuilding, exporting, deploying —
is mechanical and lives here so it behaves identically every time.

Validation is IMPORTED from load.py rather than reimplemented. Two copies
of the rules would drift, and the moment they disagree the checks are
worthless.

    python tools/ingest.py --from new.json            # validate + write + rebuild
    python tools/ingest.py --from new.json --dry-run  # validate only
    python tools/ingest.py --from new.json --deploy   # ...and commit + push
    cat new.json | python tools/ingest.py             # stdin also works

Input: a single match object, or an array of them, in exactly the shape
used by data/matches.json.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import load as loader          # noqa: E402  -- single source of validation truth

DATA = ROOT / "data" / "matches.json"


# ── file writing ────────────────────────────────────────────────────
def dump_matches(payload: dict) -> str:
    """
    Re-serialise matches.json keeping one player per line. json.dump with
    an indent would explode a 70-row file into ~1500 lines and make every
    future diff unreadable.
    """
    enc = lambda o: json.dumps(o, ensure_ascii=False)
    out = ["{"]
    for key in ("_comment", "_aliases_comment"):
        if key in payload:
            out.append(f'  {enc(key)}: ' +
                       json.dumps(payload[key], ensure_ascii=False, indent=2)
                       .replace("\n", "\n  ") + ",")
            out.append("")
    if "aliases" in payload:
        out.append('  "aliases": [')
        for i, a in enumerate(payload["aliases"]):
            out.append("    " + enc(a) + ("," if i < len(payload["aliases"]) - 1 else ""))
        out.append("  ],")
        out.append("")
    out.append('  "matches": [')
    ms = payload["matches"]
    for i, m in enumerate(ms):
        out.append("    {")
        for k in [k for k in m if k != "players"]:
            out.append(f"      {enc(k)}: {enc(m[k])},")
        out.append('      "players": [')
        for j, p in enumerate(m["players"]):
            out.append("        " + enc(p) + ("," if j < len(m["players"]) - 1 else ""))
        out.append("      ]")
        out.append("    }" + ("," if i < len(ms) - 1 else ""))
    out.append("  ]")
    out.append("}")
    return "\n".join(out) + "\n"


# ── checks ──────────────────────────────────────────────────────────
REQUIRED = ("source_ref", "winning_side", "players")


def check(new: list, existing: list) -> list:
    """Every reason this batch must not be written. Empty list == good."""
    errs = []
    seen_ref = {m["source_ref"] for m in existing}
    seen_fp = {loader.fingerprint(m): m["source_ref"] for m in existing}
    seen_id = {m.get("dota_match_id") for m in existing if m.get("dota_match_id")}

    for m in new:
        for f in REQUIRED:
            if f not in m:
                errs.append(f"missing required field {f!r}")
        if errs:
            return errs

        ref = m["source_ref"]

        # load.py's own rules: roster size, arithmetic chain, duplicate names.
        for p in loader.validate(m):
            if "ERROR" in p or "expected" in p or "duplicate" in p:
                errs.append(p)
            else:
                print(f"  [note] {p}")

        if ref in seen_ref:
            errs.append(f"{ref}: source_ref already exists — pick a new one")
        seen_ref.add(ref)

        fp = loader.fingerprint(m)
        if fp in seen_fp:
            errs.append(f"{ref}: identical roster and K/D/A to {seen_fp[fp]!r} — "
                        f"this match is already recorded")
        seen_fp[fp] = ref

        mid = m.get("dota_match_id")
        if mid and mid in seen_id:
            errs.append(f"{ref}: dota_match_id {mid} already recorded")
        if mid:
            seen_id.add(mid)

    return errs


def run(cmd: list) -> bool:
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in (r.stdout or "").splitlines():
        print("    " + line)
    if r.returncode != 0:
        for line in (r.stderr or "").splitlines():
            print("    ! " + line)
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", help="JSON file; omit to read stdin")
    ap.add_argument("--dry-run", action="store_true", help="validate only")
    ap.add_argument("--deploy", action="store_true", help="git commit and push after rebuild")
    ap.add_argument("--amend", action="store_true",
                    help="merge fields into EXISTING matches, matched on source_ref, "
                         "instead of adding new ones")
    args = ap.parse_args()

    raw = Path(args.src).read_text(encoding="utf-8") if args.src else sys.stdin.read()
    incoming = json.loads(raw)
    new = incoming if isinstance(incoming, list) else [incoming]

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    existing = payload["matches"]

    # Amending happens when a match's other post-game tab turns up later and
    # supplies the header it was missing -- match id, duration, game mode.
    # It is a merge, not a replace, so a patch can name three fields and
    # leave the roster alone.
    if args.amend:
        by_ref = {m["source_ref"]: m for m in existing}
        merged = []
        for patch in new:
            ref = patch.get("source_ref")
            if ref not in by_ref:
                print(f"\n  REFUSED — no match with source_ref {ref!r}")
                return 1
            target = by_ref[ref]
            changed = {k: (target.get(k), v) for k, v in patch.items()
                       if k != "source_ref" and target.get(k) != v}
            target.update({k: v for k, v in patch.items() if k != "source_ref"})
            merged.append((ref, changed))

        # Amend used to run a weaker check than append: only "ERROR" lines,
        # skipping roster-size and duplicate-name problems, and never the
        # fingerprint or id checks at all. Since players, scores and
        # winning_side are all amendable, that let one match be edited into
        # a copy of another -- or a winner flipped -- with no objection.
        # Validate the amended match exactly as a new one, against all the
        # others.
        errs = []
        for ref, _ in merged:
            others = [m for m in existing if m["source_ref"] != ref]
            errs += check([by_ref[ref]], others)
        if errs:
            print("\n  REFUSED — nothing was written; the amended match "
                  "does not validate:")
            for e in errs:
                print(f"    x {e}")
            return 1

        flips = [(ref, ch) for ref, ch in merged if "winning_side" in ch]
        for ref, ch in flips:
            old, new = ch["winning_side"]
            print(f"\n  !! {ref}: winning_side {old!r} -> {new!r} — this inverts "
                  f"win/loss for all ten players in that match.")

        print(f"\n  amending {len(merged)} match(es)")
        for ref, changed in merged:
            print(f"    {ref}")
            for k, (old, val) in changed.items():
                shown = str(old)[:38] + ("..." if len(str(old)) > 38 else "")
                newv = str(val)[:38] + ("..." if len(str(val)) > 38 else "")
                print(f"      {k}: {shown!r} -> {newv!r}")
        if args.dry_run:
            print("\n  dry run — nothing written.")
            return 0
        DATA.write_text(dump_matches(payload), encoding="utf-8")
        json.loads(DATA.read_text(encoding="utf-8"))
        print(f"\n  updated {DATA.relative_to(ROOT)}")
        py = sys.executable
        if not run([py, "load.py"]):        return 2
        if not run([py, "export_web.py"]):  return 3
        if args.deploy:
            refs = ", ".join(r for r, _ in merged)
            run(["git", "add", "-A"])
            run(["git", "commit", "-m", f"Amend match(es): {refs}\n\n"
                 f"Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"])
            run(["git", "push", "origin", "main"])
        return 0

    print(f"\n  validating {len(new)} match(es) against {len(existing)} already recorded")
    errs = check(new, existing)
    if errs:
        print("\n  REFUSED — nothing was written:")
        for e in errs:
            print(f"    x {e}")
        return 1

    for m in new:
        r, d = m.get("radiant_score"), m.get("dire_score")
        print(f"    ok {m['source_ref']}: {r}-{d}, {m['winning_side']} win, "
              f"{len(m['players'])} players")

    if args.dry_run:
        print("\n  dry run — nothing written.")
        return 0

    payload["matches"] = existing + new
    DATA.write_text(dump_matches(payload), encoding="utf-8")
    json.loads(DATA.read_text(encoding="utf-8"))     # must still parse
    print(f"\n  appended to {DATA.relative_to(ROOT)}")

    py = sys.executable
    if not run([py, "load.py"]):      return 2
    if not run([py, "export_web.py"]): return 3

    if args.deploy:
        refs = ", ".join(m["source_ref"] for m in new)
        if not run(["git", "add", "-A"]):
            return 4
        msg = (f"Add {len(new)} match(es): {refs}\n\n"
               f"Validated against team kills <= team score <= enemy deaths, "
               f"checked for duplicate rosters, then rebuilt and re-exported.\n\n"
               f"Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>")
        if not run(["git", "commit", "-m", msg]):
            return 5
        if not run(["git", "push", "origin", "main"]):
            return 6
        print("\n  deployed — GitHub Pages will rebuild in about a minute.")
    else:
        print("\n  not deployed. Re-run with --deploy, or commit and push yourself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
