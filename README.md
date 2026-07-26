# Total Dota Lobby Stats

### → **[Live dashboard](https://muhammadut.github.io/total-dota-lobby-stats/)**

SQLite database tracking win/loss records per player across inhouse matches,
transcribed from post-game screenshots, with a static dashboard published to
GitHub Pages.

The dashboard filters by year, so each season stands on its own — standings,
averages and hero records are all recomputed in the browser from whichever
matches are in scope. Hero portraits come from Valve's Dota 2 CDN; the slug
map in `docs/heroes.js` is HEAD-verified against that CDN at build time.

## Files

| File | Purpose |
|---|---|
| `data/matches.json` | **Source of truth.** Edit this to add matches. |
| `schema.sql` | Tables + reporting views. Safe to re-run. |
| `load.py` | Rebuilds the DB from the JSON. Idempotent. |
| `stats.py` | Terminal reports. |
| `export_web.py` | Dumps the DB to `docs/data.js` for the dashboard. |
| `docs/` | The published site (GitHub Pages serves this folder). |
| `dota_stats.db` | Generated. Not committed — rebuild with `load.py`. |

No installs needed — Python's bundled `sqlite3` module does the work, and the
dashboard is three static files with no build step.

## Adding matches

**Just say "add these" and point at the screenshots.** The `add-match` skill
(`.claude/skills/add-match/`) runs the whole pipeline: read the screenshots,
extract the fields, verify the arithmetic, check for duplicates and renamed
players, write, rebuild, and publish.

The split is deliberate:

| Step | Who | Why |
|---|---|---|
| Read the screenshot | model | judgement — reading digits off game art |
| Verify + write + deploy | `tools/ingest.py` | mechanical — must behave identically every time |

```bash
python tools/ingest.py --from new.json --dry-run   # validate only
python tools/ingest.py --from new.json --deploy    # write, rebuild, commit, push
```

Ingest writes **nothing** if any match fails. It refuses on a roster that
isn't 5-a-side, an arithmetically impossible kill/score/death ordering, a
duplicate `source_ref` or `dota_match_id`, or a **content fingerprint** matching
a match already recorded — the guard against logging the same game twice from
a re-capture. Its rules are `import`ed from `load.py` rather than reimplemented,
so the two can never drift apart.

`tools/crop.py` magnifies regions for reading ambiguous digits:

```bash
python tools/crop.py shot.png --rows --tab scoreboard   # standard bands
python tools/crop.py shot.png --grid                    # find coordinates
python tools/crop.py shot.png 3080 1370 360 70 --scale 6
```

### Publishing by hand

```bash
python load.py        # JSON  -> dota_stats.db
python export_web.py  # DB    -> docs/data.js, and re-stamps the asset hash
git add -A && git commit -m "Add matches" && git push
```

GitHub Pages redeploys automatically.

## Usage

```bash
python load.py                    # build / refresh the database
python stats.py                   # win/loss record, most-played first
python stats.py record winrate    # ...ranked by win rate instead
python stats.py log               # match log, newest first
python stats.py player UT         # one player's match-by-match history
python stats.py match 8912312189  # full scoreboard for one match
python stats.py heroes            # hero picks and win rates
python stats.py heroes UT         # ...for one player
python stats.py aliases           # flag possible duplicate identities
```

## Capturing screenshots

**The two post-game tabs are complementary — neither is sufficient alone.**

| | OVERVIEW tab | SCOREBOARD tab |
|---|---|---|
| Match ID, date, duration, mode | ✅ | ❌ |
| Team names, score, winner | ✅ | ✅ |
| Player names, net worth, K/D/A | ✅ | ✅ |
| Hero names and levels | ❌ (art only) | ✅ |
| LH/DN, GPM, XPM, damage, healing | ❌ | ✅ |
| Bounty runes, outposts, damage taken | ❌ | ✅ |
| Items / backpack / neutral | ❌ | ⚠️ icons only |

Send **both tabs per match** for full fidelity. Scoreboard alone still yields
a complete win/loss record — it just arrives with no match ID, date or
duration, like the `screenshot-2` row.

Two caveats on the scoreboard tab:
- **Items are icons, never text.** They are not transcribed; guessing item
  art is far less reliable than reading digits, and a wrong item is worse
  than no item. The schema has no items table for that reason.
- **The column strip scrolls horizontally.** Anything past *Damage Received*
  is off-screen unless you scroll right before capturing.

## Adding a match

Append an entry to `data/matches.json`, then run `python load.py`.
Re-running never duplicates: each match carries a unique `source_ref`, and
the loader upserts on it.

The loader validates before writing and reports:
- `ERROR` — a match without exactly 10 players / 5 per side, the same player
  twice in one match, or a kill/death/score combination that is
  arithmetically impossible (see *The arithmetic checksum* below)
- `note` — a legal but non-obvious gap, e.g. a tower-credited kill

Neither blocks the load; they're there so a mistyped digit is caught at
entry rather than quietly skewing a win rate later.

## Design decisions

**Win/loss is derived, never stored.** `matches.winning_side` is the single
place a result lives. `v_results` computes `won` as
`player.side == match.winning_side`. If a result is ever corrected, all ten
player rows follow automatically — there are no stale copies to disagree
with each other.

**The grain is one row per (match, player).** Aggregates like win rate are
views over that, not stored columns. Any summary can be recomputed; a
summary stored as raw data cannot be un-aggregated.

**`result_confidence` marks inferred results.** A deduced winner is never
laundered into a stated fact.

**Nullable stat columns.** Dota's OVERVIEW tab shows only name / net worth /
KDA, while the SCOREBOARD tab shows the full line (GPM, XPM, last hits,
hero damage…). Both are valid sources; one is just richer, so the extra
columns are nullable rather than being forced to zero.

## Identity handling

Steam display names change between games, which is the main threat to
accuracy here — the same human under two names splits into two records, and
both win rates become wrong.

Near-certain matches are merged automatically and reported; weaker ones are
referred to you. `python tools/automerge.py` applies the bar below:

| Signal | Verdict |
|---|---|
| Identical once case, punctuation and clan tag are stripped (`TigerX` / `____Tiger X____`) | auto |
| One stem is a prefix of the other, ≥4 chars (`vAnzO` / `vAnzOr`) | auto |
| One stem contained in the other, ≥4 chars, **same clan tag** (`¤GerM¤` / `dotagerm`) | auto |
| ≥85% similar | auto |
| Same clan tag but only 30–85% similar | **ask** |

**One precondition is never overridden:** the two names must never appear in
the same match. One person cannot hold two slots in one game, so co-occurrence
proves they are different people however alike the strings look — that is what
keeps `Thekra [UGI]` separate from `.......... [UGI]`.

The rules are calibrated against every merge decided so far (12/12): they catch
all four a string rule could find, and reject the ones that needed a human.

Merges are written to the `aliases` array in `data/matches.json` — durable,
diffable, undone by deleting a line — never applied straight to the database.
The raw observed names stay in the `players` table; reporting views resolve
through `merged_into`, so nothing is rewritten or lost.

### Confirmed merges

Merges live in the `aliases` block of `data/matches.json`, so they are
reproducible from source — rebuilding the DB from scratch reapplies them,
and deleting a line undoes one. `load.py` refuses any merge where the two
names appear in the same match, or that would form a chain (`A→B→C`), which
would break the single-level resolution in `v_player`.

| Canonical | Alias | How it was identified |
|---|---|---|
| `Stoic` | `Stoic (Mode: Sccc)(only solo)` | detector — prefix containment |
| `vAnzO` | `vAnzOr` | detector — prefix containment, 91% similar |
| `¤GerM¤ [LIB0G]` | `dotagerm [LIB0G]` | detector — shared clan tag + name root |
| `Mandark [<MC>]` | `cpx22 [<MC>]` | **user-asserted** — undetectable |
| `samundar khan` | `rtz [FUBU]` | **user-asserted** — undetectable |
| `Kael™` | `Dawn of War` | **user-asserted** — undetectable |

Half of these were user-asserted and share no name root at all —
`samundar khan`/`rtz` and `Kael™`/`Dawn of War` share neither a character
nor a clan tag. **No string-matching heuristic can find those.** Automated
detection is a net for the easy cases; ground truth comes from someone who
knows the group. That's exactly why merges are declared data rather than
inferred at query time.

## Data notes

Source: seven post-game screens, all from **2026-07-24** — five PC
screenshots of UT's client, plus two phone photos of another player's client
(UT did not play in those two).

### The arithmetic checksum

Every transcribed match is verified against this invariant:

```
sum(team kills)  <=  team score  <=  sum(enemy team deaths)
```

- **Left gap:** a hero killed by a *tower* credits the owning team's score,
  but no player is credited with the kill.
- **Right gap:** a hero killed by *neutrals, Roshan, or itself* credits
  nobody, so that death never reaches any team's score.

Usually all three quantities are equal. A violation of the *ordering* is
arithmetically impossible and means a digit was read wrong — `load.py`
reports those as `ERROR`. Gaps are reported as `note` and are legal.

Checking one team's deaths against the other team's kills is the strong
form of this test, because it validates the death column too — kills-vs-score
alone can't.

1. **Match `8912161291` — RESOLVED. No inferred results remain.**
   Its OVERVIEW header rendered "Victory" after *both* team names, so the
   winner had to be inferred from the glow-banner and the 48–41 kill lead.
   The SCOREBOARD capture settled it: `Pyaray Pairo Wali Bachiya` (Radiant)
   carries an explicit WINNER badge — the inference was correct, and the
   match is now `result_confidence = 'confirmed'`.
   That capture also showed Dire as the generic **"The Dire"**, not
   "Kaya He Karna Hai?" as the overview claimed, so the overview's label was
   stale text. `dire_team_name` was corrected to NULL. Lesson: **when the two
   tabs disagree, the scoreboard wins** — it is structured data with an
   explicit winner badge, not free-form header text.

2. **Match `8912219643` — the one match with checksum gaps. Verified, not
   an error.** Dire: 45 player kills ≤ score 46 ≤ 46 Radiant deaths (one
   Radiant hero died to a Dire tower). Radiant: 17 kills ≤ score 17 ≤ 18
   Dire deaths (one Dire hero died to neutrals/Roshan/self, crediting
   nobody). Both digits were re-checked against the source at 6× zoom.
   Every other match has zero gaps.

3. **Timestamps are not comparable across clients.** The two photo-sourced
   matches carry the *other* player's local clock (that client renders
   `M/D/YYYY` + 12-hour; UT's renders `D/M/YYYY` + 24-hour). Their match
   ids — which are globally sequential and therefore authoritative — place
   both matches *before* `8912161291`, contradicting their later wall-clock
   readings. `stats.py log` detects and reports this automatically.
   **Order by match id, not by timestamp.** Tell me your timezone offset
   and I can normalise the two photo timestamps.

4. **The screenshot-2 match has no match ID, start time, or duration.**
   It was captured on the SCOREBOARD tab, which doesn't show the match
   header. The date is assumed from the same-session browsing timestamp.
   It is the only match with full per-player stat lines and identified
   heroes. Backfill `dota_match_id` in the JSON if you find it.

5. **Hero coverage is complete — 70/70 rows.** Every match has since been
   re-captured from the SCOREBOARD tab, which prints hero names as text.
   Also complete at 70/70: hero level, last hits, denies, GPM, XPM, bounty
   runes, healing. At 50/70: outposts, damage dealt, damage received — the
   20 gaps are the two 1080p captures from another player's client, where
   those columns sit off the right edge of the screen.

   One correction came out of that re-capture: `Soooze [<MG>]` (read from a
   blurry phone photo) is actually `Soooze [<MC>]`.

6. **Match IDs are not in OpenDota** (checked — all three return
   `Not Found`). Private lobby games aren't published there, so the
   screenshots are the only source. Automatic import isn't an option for
   these; entry stays manual.

## Pulling screenshots from Discord

`tools/discord_pull.py` downloads new image attachments from one Discord
channel into `inbox/`, ready for the `add-match` skill. Fetch only — parsing a
scoreboard is a judgement task and stays with the model.

```bash
python tools/discord_pull.py            # pull anything new
python tools/discord_pull.py --list     # show, don't download
python tools/discord_pull.py --all      # ignore the watermark
```

It uses the plain REST API over `urllib` — no `discord.py`, no gateway socket,
no dependencies. A watermark file records the last message seen, so re-running
never re-downloads.

**Setup, once:**

1. [discord.com/developers/applications](https://discord.com/developers/applications) → New Application → Bot
2. Enable **Message Content Intent**, copy the token
3. OAuth2 → URL Generator → scope `bot`, permissions **View Channel** + **Read Message History** → open the URL, add it to the server
4. Right-click the channel → **Copy Channel ID** (needs Developer Mode)

Then create `tools/discord.local.json`:

```json
{"token": "...", "channel_id": "..."}
```

> **This repository is public.** A leaked bot token lets anyone drive the bot.
> `tools/discord.local.json` and `inbox/` are both gitignored — verify with
> `git check-ignore -v tools/discord.local.json` before your first push. If a
> token ever does get pushed, regenerate it in the developer portal; rotating
> is the only real fix.

A user token instead of a bot token would be self-botting, which violates
Discord's terms — don't.
