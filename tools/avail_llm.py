"""
Read the !avail messages the regex parser could not, using a model.

    python tools/avail_llm.py                  # show the queue + what I'd do
    python tools/avail_llm.py --apply          # write them + reply in Discord
    python tools/avail_llm.py --text "..." --player "Stoic"   # one-off, no queue
    python tools/avail_llm.py --drop 3         # give up on one entry

WHY THIS IS A SEPARATE SCRIPT
-----------------------------
tools/discord_league.py answers a command in ~3 seconds and must never
depend on anything slower or less certain than a regex. A model call is
both. So the bot's only job on a parse failure is to KEEP the message
(data/avail_pending.json) and say so; reading it happens here, when a
human runs this.

WHAT THE MODEL IS AND IS NOT ALLOWED TO DO
------------------------------------------
It rewrites free text into the canonical `!avail` grammar. That is all.
The string it produces is then fed through the SAME `_parse_avail()` the
bot uses, and written through the SAME `do_avail()`, so every existing
guard still applies -- roster membership, declared timezone, no dates in
the past. The model cannot emit a window directly, so it cannot express a
time the deterministic parser would have refused.

On top of that this script refuses a rewrite that:
  * does not parse                       -> the grammar is the contract
  * lands outside the current week        -> catches "next Saturday" drift
  * comes back `confidence: low`          -> unless --include-low

and everything it does accept is echoed into the channel as an
interpretation, quoting the player's original words, so the person who
typed it is the last check. Two humans see the number before it counts:
whoever runs this, and the player reading the reply.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import discord_league as L                                   # noqa: E402

MODEL = "claude-sonnet-5"


# ── The prompt ────────────────────────────────────────────────────────

PROMPT = """\
You convert one Dota player's free-text availability into a strict format.

CONTEXT
  Today is {today} (a {today_dow}).
  The league week runs {week_start} to {week_end}. Only these dates exist.
  The player is "{player}" and their declared timezone is {tz}.
  All times they write are in THEIR OWN local time. Do NOT convert
  timezones. Do NOT do any arithmetic on the clock.

THEIR MESSAGE
  <<<{raw}>>>

  (An automatic parser already refused it with: {err})

OUTPUT FORMAT
  Rewrite it as comma-separated entries, each exactly:
      <month> <day> <start> to <end>
  e.g.  aug 4 9pm to 6am, aug 5 9pm to 6am, aug 8 7pm to 11pm

  Rules:
    * One entry per calendar date. Expand "every day", "weekends",
      "Fri Sat Sun" etc. into an explicit entry for each date in the
      league week above.
    * A window that runs past midnight is written as-is on the START
      date: "9pm to 6am" means 9pm that evening until 6am the next
      morning. Never split it yourself.
    * Use 12-hour times with am/pm, or 24-hour HH:MM. Nothing else.
    * Only use dates inside the league week. If they name a day of the
      week, map it to that day's date within the league week.

  WHAT NOT TO DO
    * Do not invent availability they did not state. If they gave one
      day, return one day.
    * Do not widen a window to be helpful. "after 9" with no end is
      "9pm to 12am", not "9pm to 6am".
    * If you cannot tell what they meant -- no times at all, a
      contradiction, or a question rather than an answer -- return
      canonical: null. That is a correct answer, not a failure.

REPLY WITH ONE JSON OBJECT AND NOTHING ELSE
  {{"canonical": "<the rewrite, or null>",
    "confidence": "high" | "low",
    "note": "<one short sentence: what you read it as, or why not>"}}

  Use "low" whenever you had to guess at anything -- a missing end time,
  an ambiguous day, a phrase you are inferring rather than reading.
"""


def build_prompt(raw: str, player: str, tz: str, err: str,
                 week_start: date, week_end: date, today: date) -> str:
    return PROMPT.format(
        today=today.isoformat(), today_dow=today.strftime("%A"),
        week_start=week_start.isoformat(), week_end=week_end.isoformat(),
        player=player, tz=tz, raw=raw, err=err or "(none)")


# ── The model call ────────────────────────────────────────────────────

def ask_model(prompt: str, model: str = MODEL, timeout: int = 240) -> dict:
    """
    Run the prompt through `claude -p` and return the parsed JSON.

    Raises RuntimeError with the raw output on anything unexpected --
    a half-understood reply must not become a silently-empty rewrite.
    """
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError(
            "`claude` is not on PATH. This script needs the Claude Code CLI "
            "(npm i -g @anthropic-ai/claude-code), same as the sync workflow.")
    p = subprocess.run([exe, "-p", "--model", model],
                       input=prompt, capture_output=True,
                       text=True, encoding="utf-8", timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"claude exited {p.returncode}: {p.stderr.strip()[:400]}")

    out = (p.stdout or "").strip()
    # Be tolerant of a fenced block; be intolerant of anything else.
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError(f"no JSON object in model output: {out[:400]!r}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"model output is not JSON ({e}): {out[:400]!r}")
    if "canonical" not in data:
        raise RuntimeError(f"model output has no `canonical` key: {out[:400]!r}")
    return data


# ── Validation: the model's answer has to survive the real parser ─────

def week_bounds(sched: dict, today: date) -> tuple[date, date]:
    """The dates a rewrite is allowed to mention."""
    w = sched.get("week_of")
    start = date.fromisoformat(w) if w else today
    if start < today:
        start = today
    return start, start + timedelta(days=8)


def vet(canonical: str, week_start: date, week_end: date,
        today: date) -> tuple[list[dict] | None, str | None]:
    """
    Returns (windows, None) if the rewrite is acceptable, else (None, why).

    The first check is simply the bot's own parser: whatever the model
    said, it has to be something a player could have typed and had
    accepted. The second is the calendar -- a model asked about "next
    Saturday" will happily produce a date outside the week, and nothing
    downstream would notice.
    """
    if not canonical or not canonical.strip():
        return None, "model returned no rewrite"
    if not re.search(r"\d", canonical):
        return None, f"rewrite contains no times: {canonical!r}"

    _, windows, err = L._parse_avail(canonical, today=today)
    if err:
        return None, f"rewrite does not parse: {err}"
    if not windows:
        return None, "rewrite parsed to zero windows"

    for w in windows:
        d = w.get("date")
        if not d:
            continue                       # weekday-only, dated at render time
        dd = date.fromisoformat(d)
        if not (week_start <= dd <= week_end):
            return None, (f"date {d} is outside the league week "
                          f"{week_start}..{week_end}")
    return windows, None


# ── Applying one entry ────────────────────────────────────────────────

def render(windows: list[dict]) -> str:
    out = []
    for w in windows:
        out.append(f"  {w.get('date') or w.get('day')} "
                   f"{w['start_local']}–{w['end_local']}")
    return "\n".join(out)


def apply_entry(entry: dict, canonical: str, note: str,
                token: str, channel: str, post: bool) -> tuple[bool, str]:
    """
    Write it through do_avail() so every normal guard runs, then say in
    the channel what was recorded and what it was read from.
    """
    dp = L.load_discord_players()
    uid, uname = entry["discord_uid"], entry["discord_name"]
    player = L.player_for_author(uid, uname, dp)

    before = (L.load_scheduling().get("availability", {})
              .get(player or "", {}).get("declared_at"))
    reply = L.do_avail(canonical, uname, uid, dp)
    after = (L.load_scheduling().get("availability", {})
             .get(player or "", {}).get("declared_at"))

    wrote = after is not None and after != before
    if not wrote:
        # do_avail refused for a reason that is not about parsing --
        # unregistered, no timezone, not on a roster. Leave it queued:
        # the rewrite was fine, the player is not ready for it yet.
        return False, reply

    banner = (f"📝 Read <@{uid}>'s message for them.\n"
              f"> {entry['raw']}\n"
              f"I understood: **{canonical}**"
              + (f"  _({note})_" if note else "") + "\n"
              f"If that's wrong, post `!avail clear` and try again.\n\n")
    if post:
        L.post_msg(token, channel, banner + reply)
    return True, banner + reply


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                        # pragma: no cover
        pass

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the accepted rewrites and reply in Discord")
    ap.add_argument("--include-low", action="store_true",
                    help="also apply rewrites the model was unsure about")
    ap.add_argument("--only", type=int, metavar="ID", help="just this queue id")
    ap.add_argument("--drop", type=int, metavar="ID",
                    help="remove an entry from the queue without applying it")
    ap.add_argument("--text", metavar="MSG",
                    help="try one message without touching the queue")
    ap.add_argument("--player", metavar="NAME",
                    help="player name for --text (defaults to a generic one)")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--no-post", action="store_true",
                    help="with --apply, write locally but stay silent in Discord")
    args = ap.parse_args()

    sched = L.load_scheduling()
    ptz = L.load_players_tz().get("players", {})
    today = datetime.now(timezone.utc).date()
    week_start, week_end = week_bounds(sched, today)

    # --- one-off, no queue --------------------------------------------
    if args.text:
        player = args.player or "a player"
        tz = ptz.get(player, "Asia/Karachi")
        data = ask_model(build_prompt(args.text, player, tz, "",
                                      week_start, week_end, today), args.model)
        canonical = data.get("canonical")
        print(f"  raw        {args.text!r}")
        print(f"  canonical  {canonical!r}")
        print(f"  confidence {data.get('confidence')}  note: {data.get('note')}")
        if canonical:
            windows, why = vet(canonical, week_start, week_end, today)
            print(f"  vet        {'OK' if windows else 'REJECTED — ' + why}")
            if windows:
                print(render(windows))
        return 0

    q = L.load_pending()

    # --- drop ---------------------------------------------------------
    if args.drop is not None:
        keep = [e for e in q["pending"] if e["id"] != args.drop]
        if len(keep) == len(q["pending"]):
            print(f"  no pending entry #{args.drop}")
            return 1
        q["pending"] = keep
        L.save_json(L.AVAIL_PENDING, q)
        print(f"  dropped #{args.drop}")
        return 0

    todo = [e for e in q["pending"]
            if args.only is None or e["id"] == args.only]
    if not todo:
        print("  queue is empty — nothing to read.")
        return 0

    token, channel = (L.league_channel() if args.apply and not args.no_post
                      else (None, None))

    print(f"  {len(todo)} queued · league week {week_start} .. {week_end}\n")
    applied = held = 0

    for e in todo:
        player = e.get("player") or e.get("discord_name") or "a player"
        tz = ptz.get(e.get("player") or "", "(unknown)")
        print(f"── #{e['id']}  {player}  [{tz}]")
        print(f"   said     {e['raw']!r}")
        print(f"   parser   {e['parser_error']}")

        if tz == "(unknown)":
            print("   SKIP     no declared timezone — they must !register first\n")
            held += 1
            continue

        try:
            data = ask_model(build_prompt(e["raw"], player, tz,
                                          e["parser_error"],
                                          week_start, week_end, today),
                             args.model)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            print(f"   ERROR    {exc}\n")
            held += 1
            continue

        canonical = (data.get("canonical") or "").strip()
        conf = (data.get("confidence") or "low").lower()
        note = (data.get("note") or "").strip()
        print(f"   read as  {canonical!r}  ({conf})")
        if note:
            print(f"   note     {note}")

        windows, why = vet(canonical, week_start, week_end, today)
        if not windows:
            print(f"   REJECT   {why}\n")
            held += 1
            continue
        print(render(windows))

        if conf != "high" and not args.include_low:
            print("   HOLD     low confidence — re-run with --include-low "
                  "to accept\n")
            held += 1
            continue

        if not args.apply:
            print("   would apply (re-run with --apply)\n")
            continue

        ok, reply = apply_entry(e, canonical, note, token, channel,
                                post=not args.no_post)
        if not ok:
            print(f"   BLOCKED  {reply.splitlines()[0]}\n")
            held += 1
            continue

        q["pending"] = [x for x in q["pending"] if x["id"] != e["id"]]
        q["resolved"].append({**e, "canonical": canonical, "note": note,
                              "confidence": conf,
                              "resolved_at": datetime.now(timezone.utc)
                              .isoformat().replace("+00:00", "Z")})
        L.save_json(L.AVAIL_PENDING, q)
        applied += 1
        print("   APPLIED  written + replied in channel\n")

    print(f"  {applied} applied, {held} still waiting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
