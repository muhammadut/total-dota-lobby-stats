# Total Dota Lobby Stats — working notes

Win/loss tracking for a private Dota 2 inhouse lobby, transcribed by hand from
post-game screenshots into SQLite and published to GitHub Pages.

**Live:** https://muhammadut.github.io/total-dota-lobby-stats/

## The one rule

**Never hand-edit `data/matches.json`, and never hand-run the deploy.**
`tools/ingest.py` does both, identically every time. Reading a screenshot is a
judgement task and belongs to the model; validating, writing, rebuilding and
deploying are mechanical and belong to the tool.

```bash
python tools/discord_pull.py                       # fetch new screenshots -> inbox/
python tools/ingest.py --from new.json --dry-run   # validate, write nothing
python tools/ingest.py --from new.json --deploy    # write, rebuild, commit, push
python tools/ingest.py --from patch.json --amend   # backfill fields on an existing match
python tools/automerge.py                          # merge obvious renames, list the rest
python tools/crop.py shot.png --rows --tab scoreboard   # zoom for ambiguous digits
```

The `add-match` skill drives all of this. Say "add these" and it runs.

## Verifying a transcription

Every match must satisfy, per team:

```
sum(team kills)  <=  team score  <=  sum(enemy team deaths)
```

Left gap = a hero killed by a **tower** (credits the team, no player).
Right gap = a hero killed by **neutrals/Roshan/itself** (credits nobody).
Usually all three are equal. **Violating the ordering is arithmetically
impossible and means a digit was misread** — re-read at zoom, don't record it.

Gaps of 3 have appeared legitimately in this lobby (they dive high ground), but
anything above 1 is worth re-reading before accepting.

**Names have no checksum.** Kill totals catch a bad digit; nothing catches a bad
letter. Zoom any name you are not certain of. `[<MG>]` turned out to be `[<MC>]`
once; `[UGI]` is really `[UGI|]`.

## Identity

Near-certain renames merge automatically and the user is **told, not asked**
(their explicit instruction). Weaker candidates get referred to them. The bar
lives in `tools/automerge.py`, calibrated 12/12 against every merge decided so
far.

**Never overridden:** two names appearing in the *same match* are proof of two
different people, however alike the strings look.

Merges are recorded in the `aliases` array of `data/matches.json` — durable,
diffable, undone by deleting a line. Never applied straight to the database.
Several real merges share no characters at all (`cpx22`/`Mandark`,
`samundar khan`/`rtz`, `Kael™`/`Dawn of War`, `Learn some basic_!_`/
`MODE:IG.BASHIRA™`); those only ever come from the user.

## Dating a match

The SCOREBOARD tab has **no match header** — no id, date, duration or mode. Only
the OVERVIEW tab does. Sources of truth, best first:

1. **OVERVIEW tab** — real match id and start time. If one turns up later for a
   match already recorded, backfill it with `ingest.py --amend`.
2. **Discord upload time** — server-side and absolute. Best clock for a
   scoreboard-only capture. Still an *upper bound* on the match itself.
3. **`Dota_2_<YYYY.MM.DD>-<HH.MM>.png` filename** — the capturing client's local
   clock, which may be a different timezone.
4. File mtime — for shared files this is arrival time, 2–3 h after the game.

**Known timezones** (confirmed against Discord timestamps): the user's client is
**UTC−4**; `stoicheart`'s is **UTC+5**, a 9-hour gap. A constant offset across
several files is a timezone, not a wrong clock.

Clients are also identifiable by the gold counter in the top bar: `67,845` is
the user's, `26,525` is hurrali's, `389,225` is stoicheart's.

## Dashboard

Light, Apple/Airtable-adjacent: paper canvas, white cards on layered shadows,
hairline rules, frosted-glass chrome. **Colour is rationed** — the only
saturated hues are Radiant green and Dire red, so they always mean something.
Fonts: Plus Jakarta Sans + DM Mono. Hero art from Valve's CDN.

- Aggregation happens **in the browser**, not the exporter, because the year
  filter recomputes every figure from the matches in scope.
- `export_web.py` re-stamps a content hash onto asset URLs. Without it a
  returning visitor can hold a cached `app.js` beside a fresh `data.js`, and the
  page renders blank. This has actually happened.
- **Don't surface internal integrity metrics in the UI.** The player-game count
  (matches × 10) was shown once and read as a contradiction next to "Matches 7".

## Cautions

- The repo is **public**. `tools/discord.local.json` holds the bot token and is
  gitignored — verify with `git check-ignore` before any push that touches it.
- Items on the scoreboard are icons, never text. They are not transcribed; a
  wrong item is worse than no item.
- Screenshots come in at least three resolutions (5120×2160, 1920×1080,
  1920×1200) and get cut at different columns. Missing columns stay NULL.
