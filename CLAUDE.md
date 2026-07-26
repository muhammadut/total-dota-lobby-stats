# Total Dota Lobby Stats — working notes

Win/loss tracking for a private Dota 2 inhouse lobby. Screenshots → SQLite →
static dashboard on GitHub Pages, with a Discord bot as the front door.

**Live:** https://muhammadut.github.io/total-dota-lobby-stats/
**Repo:** github.com/muhammadut/total-dota-lobby-stats (PUBLIC)

**The point of the whole system:** at the end of the year, look at the
accumulated data and say who won the most matches. So the failure that matters
is *a wrong number produced quietly and surviving twelve months*. A loud crash
is fine. Silence is not. Every guard in this codebase leans that way.

## The one rule

**Never hand-edit `data/matches.json`, and never hand-run the deploy.**
Reading a screenshot is judgement and belongs to the model; validating, writing,
rebuilding and deploying are mechanical and belong to the tools.

```bash
python tools/discord_pull.py            # Discord -> inbox/
python tools/discord_commands.py        # act on channel instructions
python tools/discord_ask.py --resolve   # apply merge votes
python tools/reconcile.py               # account for every downloaded image
python tools/ingest.py --from f.json --dry-run | --deploy | --amend
python tools/automerge.py [--ask-discord]
python tools/crop.py shot.png --rows --tab scoreboard
python load.py && python export_web.py
```

The `add-match` skill drives the screenshot path. Say "add these".

## Verifying a transcription

```
sum(team kills)  <=  team score  <=  sum(enemy team deaths)
```

Left gap = a tower kill (credits the team, no player). Right gap = death to
neutrals/Roshan/self (credits nobody). Usually all three are equal. Violating
the *ordering* is arithmetically impossible → a digit was misread. Gaps of 3
have occurred legitimately (this lobby dives high ground), but re-read anything
above 1 at zoom before accepting it.

**Only kills, deaths and score are checksummed.** Names, net worth, GPM, assists
have no verification at all — a misread name silently mints a new player. Zoom
any name you are not certain of.

## Identity — where the year-end number actually breaks

Near-certain renames merge automatically and the user is **told, not asked**
(their explicit instruction). Weaker candidates become a Discord question.
The bar lives in `tools/automerge.py`, calibrated 12/12 against known merges.

**Absolute precondition, never overridden:** two names in the *same match* are
two different people. `load.py` enforces this **transitively** — an incoming
alias is checked against the canonical *and everyone already merged into it*.
Skipping that let one player show 12 games across 10 real matches.

Merges live in the `aliases` array of `data/matches.json`. Never applied
straight to the database. Several real merges share no characters at all
(`cpx22`/`Mandark`, `samundar khan`/`rtz`, `Kael™`/`Dawn of War`,
`Learn some basic_!_`/`MODE:IG.BASHIRA™`) — those only ever come from a human.

## Dating a match

SCOREBOARD tab has **no match header**. Sources, best first:

1. **OVERVIEW tab** — real match id and start time. Found later? `ingest.py --amend`.
2. **Discord upload time** — server-side, absolute. Best for scoreboard-only.
3. **`Dota_2_<date>-<time>.png` filename** — the *capturing client's* clock.
4. File mtime — for shared files this is arrival, 2–3 h after the game.

**Confirmed timezones:** user is **UTC−4**; `stoicheart` is **UTC+5** (9 h gap).
A constant offset across several files is a timezone, not a broken clock.
Clients are identifiable by the gold counter: `67,845` user, `26,525` hurrali,
`389,225` stoicheart.

## Discord

Bot `DotaLobbyStats#6201` in `#lobby-stats` (channel `1530411273895018668`).
Token in `tools/discord.local.json`, **gitignored — the repo is public**.

Approvers (only their votes/commands change identity): `ut70`
`539898067957186560`, `fzr2k` `364832153843793920`, `stoicheart`
`1149675111557890048`.

Channel understands: `merge A and B`, `not same A and B`, `yes`/`no` (as a
reply), `who is A`, `status`, `help`. Names resolve loosely; ambiguity is
reported, never guessed.

**Two independent watermarks** — `data/discord_watermark.txt` (images) and
`data/discord_cmd_watermark.txt` (commands). They must stay separate or one
reader swallows the other's messages. `data/discord_seen.json` records the fate
of every downloaded image, which is what makes the image watermark safe to
advance past chat.

## Automation

`.github/workflows/sync-matches.yml` — daily 09:00 UTC + manual dispatch.
Pull → if nothing new, exit before starting the model → else Sonnet parses,
ingests, and the workflow commits and pushes. Public repo = free Actions
minutes. `--limit 12` caps a single run so a dump of images can't become a bill.

**Order matters: `load.py` runs BEFORE `discord_commands.py`.** `dota_stats.db`
is gitignored, so a fresh runner has no database — without that first build,
every merge command answers "I don't have a player called X" and `co_occur()`
returns False, disabling the co-occurrence guard.

## Status — as of 2026-07-25

14 matches, 40 player rows / 30 people, 10 merges, all from 2026-07-24/25.
Every match is 5v5, none is dateless, and the live asset hash matches the
built one.

**The pipeline has now been rehearsed end-to-end, locally, on two real
Discord screenshots.** Pull → triage → zoom-verify → ingest → automerge →
reconcile → deploy, with the live site byte-identical to the build (modulo
git's CRLF→LF). Two things that only a real run surfaced:

- **A re-read agreed with the recorded match digit for digit.** `discord-
  1530753840532688998` had already been ingested; transcribing it again from
  scratch reproduced all 10 players, 30 K/D/A values, both scores and the
  winner exactly. That is the only real evidence the reading step is stable.
- **`Rogue Agent [ITIzI]` was a misread of `Rogue Agent [|T|z|]`** — pipes,
  not capital I, settled at 12× where the bars overshoot the `T`'s cap-height
  above and below. One human had been split across two rows for 2 games.
  `automerge.py` caught it unprompted once both spellings existed.

**The cloud workflow has still NEVER RUN.** No repo secrets are set. Needed:
`DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_APPROVERS`
(`539898067957186560,364832153843793920,1149675111557890048`), and
`CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) or `ANTHROPIC_API_KEY`.

Triage has never seen a hostile image, and the command parser has only ever
been driven by Claude. **Next step: post a deliberately bad image — wrong
tab, cropped, not Dota at all — and confirm it is refused and answered in
channel rather than silently dropped.**

### `automerge.py` picks the established name as canonical

When it merged the two `Rogue Agent` rows it kept `[ITIzI]` — the *misread*
spelling — because that row had the earlier appearances. The count is right
(one person, 2 games) but the dashboard shows a name that was never real.
There is no command to flip a merge's direction; the only route is editing
the `aliases` line and re-running `load.py`. Same root as known-unfixed 4.

## Known-unfixed (adversarial review, 2026-07-25)

Three reviewers audited ingest, identity and automation. The critical findings
are fixed and verified against reproductions. These remain **open**:

1. **Non-atomic writes.** Every writer uses `Path.write_text` (truncate). A
   crash mid-write leaves the year's ledger empty or half-written; git is the
   only recovery. Wants temp-file + `os.replace`.
2. **`norm()` erases non-ASCII.** `毒奶粉2024` and `开心果2024` both reduce to
   `2024` and would auto-merge. Also `Player1`/`Player2` at 86% similarity.
   Needs a minimum *alphabetic* stem, not just 4 characters.
3. **Approvals fail open in CI.** `tools/discord.local.json` is gitignored, so
   if `DISCORD_APPROVERS` is unset the approver list is empty and
   `privileged = (not allow)` makes **everyone** an approver. Set the secret.
4. **`not same A and B` doesn't un-merge.** It records the rejection and says
   "I won't ask again", but if they were already merged the merge stays. There
   is no un-merge command; only deleting the alias line and re-running `load.py`.
5. A stray `yes` from an approver applies the single open merge question even
   if it wasn't a reply to it.
6. `dump_matches()` silently drops unknown top-level keys in `matches.json`.
7. A dateless match (`played_on` null) is counted in **every** year by the
   dashboard filter. Currently 0 such matches, but it would corrupt a year-end
   board.

## Cautions

- Items on the scoreboard are icons, never text. Not transcribed — a wrong item
  is worse than no item.
- Screenshots arrive at 5120×2160, 1920×1080 and 1920×1200, cut at different
  columns. Missing columns stay NULL.
- `export_web.py` re-stamps a content hash onto asset URLs. Without it a
  returning visitor can hold a cached `app.js` beside a fresh `data.js` and the
  page renders blank. This has actually happened.
- Don't surface internal integrity metrics in the UI. The player-game count
  (matches × 10) was shown once and read as a contradiction next to "Matches 7".
- Dashboard is light, Apple/Airtable-adjacent; colour is rationed so Radiant
  green and Dire red always mean something.
