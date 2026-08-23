/* ═══════════════════════════════════════════════════════════════════
   Total Dota Lobby Stats — view layer.
   Reads window.LOBBY (data.js) and window.HERO_SLUG (heroes.js).
   No framework, no build step: three static files GitHub Pages can
   serve forever.

   Standings and hero records are aggregated HERE rather than in the
   exporter, because the year filter has to recompute every average and
   win rate from whatever subset of matches is showing.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  var D = window.LOBBY, SLUG = window.HERO_SLUG || {};
  if (!D) { console.error("data.js did not load"); return; }

  var CDN = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var el = function (t, c, h) {
    var n = document.createElement(t);
    if (c) n.className = c;
    if (h !== undefined) n.innerHTML = h;
    return n;
  };
  // Everything from the data file passes through here before touching
  // innerHTML. Names in this lobby include "[<MC>]", "¤GerM¤" and "™".
  var esc = function (s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  };
  var num = function (n) { return n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US"); };
  var dur = function (s) { return s ? Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") : "—"; };
  var pct = function (n) { return n === null || n === undefined ? "—" : n.toFixed(1) + "%"; };
  var rat = function (n) { return n === null || n === undefined ? "∞" : n.toFixed(2); };

  var MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  function pretty(iso) {
    if (!iso) return "—";
    var p = iso.split(" "), d = p[0].split("-");
    var s = Number(d[2]) + " " + MON[Number(d[1]) - 1] + " " + d[0];
    return p[1] ? s + " · " + p[1] : s;
  }

  /* Hero art. icons/ ≈5 KB for inline avatars, full portrait ≈50 KB for
     the hero cards, renders/ (transparent) for the masthead band. */
  function heroSrc(hero, kind) {
    var s = SLUG[hero];
    if (!s) return "";
    if (kind === "icon")   return CDN + "/icons/" + s + ".png";
    if (kind === "render") return CDN.replace("/images/", "/videos/") + "/renders/" + s + ".png";
    return CDN + "/" + s + ".png";
  }
  function faceTag(hero, cls, kind) {
    var src = heroSrc(hero, kind || "icon");
    if (!src) return '<span class="' + cls + '"></span>';
    return '<img class="' + cls + '" src="' + src + '" alt="" loading="lazy" decoding="async"' +
           ' onerror="this.style.visibility=\'hidden\'">';
  }

  /* ── Rating ───────────────────────────────────────────────────────
     One number that orders the board without anyone having to pair a
     win rate with a "5+ games" filter by hand.

     Raw win rate cannot do it: a 1-0 record scores 100% and outranks
     every real season. A minimum-games cutoff only moves the problem —
     it is an arbitrary line, and it hides the people below it entirely.

     So rank by the LOWER BOUND of a 95% confidence interval on the win
     rate (Wilson). It answers "what win rate does this record actually
     support?", and evidence moves it on its own: 1-0 supports only
     20.7%, while 18-8 over 26 games supports 50.0%. More games at a
     good rate therefore always beats fewer games at a great one, with
     no threshold anywhere.

     50% is the exact population mean here — every match has five
     winners and five losers — so a score above 50 is genuinely and
     measurably above lobby average, not merely above the sample.

     z is how much certainty the score demands, and it is therefore the
     dial for how heavily games played counts. At z=1.96 a 3-1 record
     (30.1) just edges a 10-10 one (29.9) — statistically fair, but it
     puts a four-game player sixth. Raising z widens the interval, which
     costs a small sample far more than a large one, so volume rises:
     at 2.576 that same 3-1 falls behind four longer records, and at 3.0
     it drops nine places. Exposed as a control because "how much should
     turning up count?" is a matter of taste, not of statistics. */
  function wilson(wins, n, z) {
    if (!n) return 0;
    var p = wins / n, z2 = z * z;
    var centre = p + z2 / (2 * n);
    var margin = z * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n));
    return Math.max(0, (centre - margin) / (1 + z2 / n)) * 100;
  }

  /* ── Aggregation ──────────────────────────────────────────────── */
  function aggregate(ms) {
    var P = {}, H = {};

    ms.forEach(function (m) {
      m.radiant.concat(m.dire).forEach(function (r) {
        var p = P[r.name];
        if (!p) {
          p = P[r.name] = { name: r.name, games: 0, wins: 0, losses: 0,
            kills: 0, deaths: 0, assists: 0, netSum: 0, netN: 0, bestNet: 0,
            gpmSum: 0, xpmSum: 0, rateN: 0, history: [], picks: {} };
        }
        p.games++;
        if (r.won) p.wins++; else p.losses++;
        p.kills += r.k; p.deaths += r.d; p.assists += r.a;
        if (r.net != null) { p.netSum += r.net; p.netN++; if (r.net > p.bestNet) p.bestNet = r.net; }
        if (r.gpm != null) { p.gpmSum += r.gpm; p.xpmSum += (r.xpm || 0); p.rateN++; }
        p.history.push({ seq: m.seq, played: m.played_at || m.played_on, won: r.won,
                         hero: r.hero, k: r.k, d: r.d, a: r.a, net: r.net });
        if (r.hero) p.picks[r.hero] = (p.picks[r.hero] || 0) + 1;

        if (r.hero) {
          var h = H[r.hero];
          if (!h) h = H[r.hero] = { hero: r.hero, picks: 0, wins: 0, losses: 0, who: {} };
          h.picks++;
          if (r.won) h.wins++; else h.losses++;
          // Per-player, not just a name set: a hero card that says
          // "picked 9 times" is only half an answer without "by whom".
          var q = h.who[r.name];
          if (!q) q = h.who[r.name] = { name: r.name, picks: 0, wins: 0, losses: 0 };
          q.picks++;
          if (r.won) q.wins++; else q.losses++;
        }
      });
    });

    var players = Object.keys(P).map(function (k) {
      var p = P[k];
      p.winPct = p.games ? (p.wins / p.games) * 100 : 0;
      // Undefined, not zero: dividing by no deaths has no answer.
      p.kda    = p.deaths ? (p.kills + p.assists) / p.deaths : null;
      p.avgGpm = p.rateN ? Math.round(p.gpmSum / p.rateN) : null;
      p.avgXpm = p.rateN ? Math.round(p.xpmSum / p.rateN) : null;
      p.avgNet = p.netN  ? Math.round(p.netSum / p.netN)  : null;
      p.history.sort(function (a, b) { return a.seq - b.seq; });
      p.heroes = Object.keys(p.picks)
        .map(function (h) { return { hero: h, picks: p.picks[h] }; })
        .sort(function (a, b) { return b.picks - a.picks || a.hero.localeCompare(b.hero); });
      p.topHero = p.heroes.length ? p.heroes[0].hero : null;
      return p;
    });

    var heroes = Object.keys(H).map(function (k) {
      var h = H[k];
      h.roster = Object.keys(h.who).map(function (n) { return h.who[n]; })
        .sort(function (a, b) { return b.picks - a.picks || b.wins - a.wins || a.name.localeCompare(b.name); });
      h.players = h.roster.map(function (q) { return q.name; });
      return h;
    }).sort(function (a, b) {
      return b.picks - a.picks || b.wins - a.wins || a.hero.localeCompare(b.hero);
    });

    return { players: players, heroes: heroes };
  }

  /* ── State ────────────────────────────────────────────────────── */
  var YEARS = D.meta.years || [];
  var state = {
    year: YEARS[0] || "all",
    // Rating, not games: it is the column that already accounts for both
    // sample size and win rate, so the default view needs no filter set.
    sortKey: "rating", sortDir: -1,
    qPlayers: "", minGames: 1,
    qHeroes: "", heroSort: "picks",
    qMatches: "", matchSort: "recent",
    duoPick: [], duoSort: "delta", duoMin: 2, qDuos: "",
    // 99%. Deliberately stricter than the textbook 95%: this is a
    // participation league, and a four-game record should not sit
    // alongside a twenty-game one.
    evidence: 2.576,
    // Schedule times default to Pakistan: it is the league's reference
    // clock, the one the slots were chosen in, and where most players are.
    fxZone: "Pakistan",
    fxView: "timeline",
    // The tournament page opens on Standings — the question people
    // actually arrive with is "who is winning the league", not "what
    // happened in game 2 of a series three weeks ago".
    srView: "standings",
    // Teams rank by POINTS by default; that is the league table. Players
    // rank by Rating, matching the lobby board they mirror.
    trSort: "points", trDir: -1,
    trPSort: "rating", trPDir: -1,
    qTour: "", tourMin: 1
  };
  var cur = { matches: [], players: [], heroes: [] };

  var hay = function (s) { return String(s || "").toLowerCase(); };
  var match = function (q, parts) {
    if (!q) return true;
    var needle = q.toLowerCase().trim();
    for (var i = 0; i < parts.length; i++) {
      if (hay(parts[i]).indexOf(needle) !== -1) return true;
    }
    return false;
  };

  // Wire a segmented pill group: sets state[key] from data-<attr> and redraws.
  function segment(id, attr, key, redraw, cast) {
    var wrap = $("#" + id);
    if (!wrap) return;
    wrap.addEventListener("click", function (e) {
      var b = e.target.closest(".seg");
      if (!b) return;
      var val = b.getAttribute("data-" + attr);
      state[key] = cast ? cast(val) : val;
      Array.prototype.forEach.call(wrap.querySelectorAll(".seg"), function (o) {
        o.classList.toggle("is-on", o === b);
      });
      redraw();
    });
  }

  // Typing should filter as you go, but redrawing 60 cards per keystroke is
  // wasteful — coalesce to one redraw per idle moment.
  function searchBox(id, key, redraw) {
    var el0 = $("#" + id);
    if (!el0) return;
    var t = null;
    el0.addEventListener("input", function () {
      clearTimeout(t);
      t = setTimeout(function () { state[key] = el0.value; redraw(); }, 120);
    });
  }

  function applyYear() {
    // A match with no recorded date has no year, and must not silently
    // vanish from every view — show it under whichever year is selected.
    cur.matches = state.year === "all"
      ? D.matches.slice()
      : D.matches.filter(function (m) { return !m.year || m.year === state.year; });
    var agg = aggregate(cur.matches);
    cur.players = agg.players;
    cur.heroes  = agg.heroes;
    rescore();
  }

  // Rating depends on a setting the reader can change, so it is stamped
  // onto each player rather than computed inside aggregate() — the sort
  // comparator, the drawer and the Duos header all read p.rating, and
  // they must never disagree about which setting produced it.
  /* The raw lower bound tops out around 44 in a season this size, which
     reads like a broken percentage sitting next to a 69.2% win rate.
     Doubling it puts the field on a 0-100 scale with a fixed, meaningful
     ceiling: 100 is where the bound reaches 50%, i.e. where a record
     PROVES its owner beats a coin flip at the chosen confidence.

     The anchor is a constant, not a normalisation against the current
     field, so a player's rating never moves because somebody else
     played a game. Doubling is monotonic, so the order you approved is
     untouched. Scores above 100 are possible and are not an error --
     they mean the case is proven with room to spare -- but only on the
     most lenient setting, where the current best is 101. */
  var SCALE = 2;
  function rescore() {
    cur.players.forEach(function (p) {
      p.rating = wilson(p.wins, p.games, state.evidence) * SCALE;
    });
  }
  // Fixed bands, because the scale itself has fixed meaning: 70 is most
  // of the way to proven, 55 is a clearly winning record with evidence
  // behind it. Below that the number is not yet saying much, so it stays
  // grey rather than being coloured red -- a 3-2 record is not a failure,
  // it is an unfinished sentence.
  function ratingTier(r) {
    return r >= 70 ? " is-elite" : r >= 55 ? " is-strong" : "";
  }

  /* ── Masthead ─────────────────────────────────────────────────── */
  // A filmstrip of the most-drafted heroes, blurred back into the paper.
  // Deliberately the 53 KB portraits, NOT the transparent renders — those
  // are ~1.7 MB each, so a seven-hero band would cost 12 MB of masthead.
  function drawBand() {
    var art = $("#bandArt");
    art.innerHTML = "";
    var pool = cur.heroes.slice(0, 12);
    if (!pool.length) return;
    var need = 14, k = 0;
    while (k < need) {
      var h = pool[k % pool.length];
      var src = heroSrc(h.hero, "art");
      if (src) {
        var img = new Image();
        img.src = src; img.alt = ""; img.decoding = "async";
        img.onerror = function () { this.remove(); };
        art.appendChild(img);
      }
      k++;
    }
  }

  function drawTally() {
    // Deliberately NOT showing the player-game count (matches x 10). It is
    // an internal integrity figure -- the loader checks that every player's
    // wins and losses sum to it -- and next to "Matches 7" a second, larger
    // count just reads as a contradiction.
    var kills = 0, longest = 0, played = 0;
    cur.matches.forEach(function (m) {
      m.radiant.concat(m.dire).forEach(function (r) { kills += r.k; });
      if (m.duration_seconds) {
        played += m.duration_seconds;
        if (m.duration_seconds > longest) longest = m.duration_seconds;
      }
    });
    var hrs = played / 3600;
    var items = [
      ["Matches", cur.matches.length, "Matches played in this lobby"],
      ["Players", cur.players.length, "Distinct people, after merging renamed accounts"],
      ["Heroes", cur.heroes.length, "Distinct heroes picked at least once"],
      ["Kills", num(kills), "Hero kills across every match"],
      ["Longest", longest ? dur(longest) : "—", "Longest match of the season"],
      ["Hours", hrs ? hrs.toFixed(1) : "—", "Total time in game, where duration was captured"]
    ];
    var wrap = $("#tally");
    wrap.innerHTML = "";
    items.forEach(function (t) {
      var d = el("div"); d.title = t[2];
      d.appendChild(el("dt", null, esc(t[0])));
      d.appendChild(el("dd", null, esc(t[1])));
      wrap.appendChild(d);
    });

    // Don't hardcode the game mode — the lobby plays Captains Mode and All
    // Pick, and a mode named in the tagline that isn't universal is a lie.
    var label = state.year === "all" ? "All time" : state.year;
    var modes = {};
    cur.matches.forEach(function (m) { if (m.game_mode) modes[m.game_mode] = 1; });
    var names = Object.keys(modes);
    var suffix = names.length === 1 ? " of " + names[0] : "";
    $("#tagline").textContent = cur.matches.length
      ? label + " · " + cur.matches.length + " matches" + suffix
      : label + " · nothing recorded yet";
  }

  function drawYears() {
    var wrap = $("#yearFilter");
    wrap.innerHTML = "";
    var opts = YEARS.slice();
    if (YEARS.length > 1) opts.push("all");
    opts.forEach(function (y) {
      var b = el("button", "year" + (y === state.year ? " is-on" : ""),
                 y === "all" ? "All time" : esc(y));
      b.setAttribute("aria-pressed", y === state.year ? "true" : "false");
      b.addEventListener("click", function () {
        if (state.year === y) return;
        state.year = y;
        renderAll();
      });
      wrap.appendChild(b);
    });
    // A lone year is a label, not a choice — but keep it visible so the
    // control doesn't appear out of nowhere when 2027 arrives.
    wrap.hidden = opts.length === 0;
  }

  /* ── Standings ────────────────────────────────────────────────── */
  function drawStandings() {
    var tb = $("#standings tbody");
    tb.innerHTML = "";
    var k = state.sortKey, dir = state.sortDir;

    var list = cur.players.filter(function (p) {
      if (p.games < state.minGames) return false;
      return match(state.qPlayers, [p.name, p.topHero]);
    }).sort(function (a, b) {
      var x = a[k], y = b[k];
      if (k === "name") return dir * String(x).localeCompare(String(y));
      if (x === null || x === undefined) x = dir === -1 ?  Infinity : -Infinity;
      if (y === null || y === undefined) y = dir === -1 ?  Infinity : -Infinity;
      if (x !== y) return dir * (x - y);
      // Ties break on evidence, then performance — so sorting by games
      // can never put a 1-4 record above a 3-2 one.
      if (a.games !== b.games)   return b.games - a.games;
      if (a.winPct !== b.winPct) return b.winPct - a.winPct;
      return String(a.name).localeCompare(String(b.name));
    });

    list.forEach(function (p, i) {
      var tr = el("tr");
      tr.style.setProperty("--i", i);
      if (i < 3 && (k === "rating" || k === "games")) tr.classList.add("is-lead");
      tr.tabIndex = 0;
      tr.setAttribute("role", "button");
      tr.setAttribute("aria-label", "Record for " + p.name);
      tr.innerHTML =
        '<td><span class="rank">' + (i + 1) + "</span></td>" +
        '<td class="c-player"><span class="who">' + faceTag(p.topHero, "who__face") +
          '<span><span class="who__name">' + esc(p.name) + "</span>" +
          (p.topHero ? '<span class="who__hero">' + esc(p.topHero) + "</span>" : "") +
          "</span></span></td>" +
        "<td>" + p.games + "</td>" +
        '<td class="w-num">' + p.wins + "</td>" +
        '<td class="l-num">' + p.losses + "</td>" +
        '<td><div class="meter"><span style="width:' + p.winPct.toFixed(1) + '%"></span></div></td>' +
        '<td class="pct">' + pct(p.winPct) + "</td>" +
        '<td class="c-kda">' + p.kills + " / " + p.deaths + " / " + p.assists + "</td>" +
        "<td>" + rat(p.kda) + "</td>" +
        '<td class="c-opt dim">' + num(p.avgGpm) + "</td>" +
        '<td class="c-rating"><span class="rating' + ratingTier(p.rating) + '">' +
          p.rating.toFixed(1) + "</span></td>";
      tr.addEventListener("click", function () { openDrawer(p); });
      tr.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDrawer(p); }
      });
      tb.appendChild(tr);
    });

    if (!list.length) {
      tb.innerHTML = '<tr><td colspan="11" class="none">' +
        'No players match that filter.</td></tr>';
    }
  }

  function wireSort() {
    Array.prototype.forEach.call(document.querySelectorAll("#standings th[data-sort]"), function (th) {
      th.addEventListener("click", function () {
        var k = th.getAttribute("data-sort");
        if (k === state.sortKey) state.sortDir = -state.sortDir;
        else { state.sortKey = k; state.sortDir = (k === "name") ? 1 : -1; }
        Array.prototype.forEach.call(document.querySelectorAll("#standings th"), function (o) {
          o.removeAttribute("aria-sort");
        });
        th.setAttribute("aria-sort", state.sortDir === 1 ? "ascending" : "descending");
        drawStandings();
      });
    });
  }

  /* ── Matches ──────────────────────────────────────────────────── */
  function board(sideCls, roster, label, won) {
    var t = el("table", "board");
    t.appendChild(el("caption", sideCls, esc(label) + (won ? " — victory" : "")));
    var head = el("tr");
    ["Player", "Lvl", "K / D / A", "Net", "LH / DN", "GPM", "XPM", "Hero dmg"]
      .forEach(function (h) { head.appendChild(el("th", null, h)); });
    var th = el("thead"); th.appendChild(head); t.appendChild(th);

    var tb = el("tbody");
    roster.forEach(function (r) {
      var tr = el("tr");
      tr.innerHTML =
        '<td><span class="b-who">' + faceTag(r.hero, "b-face") +
          '<span><span class="b-name">' + esc(r.name) + "</span><br>" +
          '<span class="b-hero">' + esc(r.hero || "—") + "</span></span></span></td>" +
        "<td>" + (r.lvl || "—") + "</td>" +
        "<td>" + r.k + " / " + r.d + " / " + r.a + "</td>" +
        "<td>" + num(r.net) + "</td>" +
        "<td>" + num(r.lh) + " / " + num(r.dn) + "</td>" +
        "<td>" + num(r.gpm) + "</td>" +
        "<td>" + num(r.xpm) + "</td>" +
        "<td>" + num(r.hdmg) + "</td>";
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    return t;
  }

  function drawMatches() {
    var wrap = $("#matches");
    wrap.innerHTML = "";

    var list = cur.matches.filter(function (m) {
      if (!state.qMatches) return true;
      var parts = [m.radiant_team_name, m.dire_team_name, m.dota_match_id];
      m.radiant.concat(m.dire).forEach(function (r) { parts.push(r.name, r.hero); });
      return match(state.qMatches, parts);
    });

    var order = state.matchSort;
    if (order === "recent")      list = list.slice().reverse();
    else if (order === "oldest") list = list.slice();
    else if (order === "kills")  list = list.slice().sort(function (a, b) {
      return (b.radiant_score + b.dire_score) - (a.radiant_score + a.dire_score);
    });

    if (!list.length) {
      wrap.innerHTML = '<p class="none">No matches contain that.</p>';
      return;
    }

    list.forEach(function (m, i) {
      var rWon = m.winning_side === "radiant";
      var card = el("div", "match");
      card.style.setProperty("--i", i);

      var head = el("button", "match__head");
      head.setAttribute("aria-expanded", "false");
      head.innerHTML =
        '<span class="match__seq">' + String(m.seq).padStart(2, "0") + "</span>" +
        '<span class="team team--radiant' + (rWon ? "" : " is-loser") + '">' +
          '<span class="team__name">' + esc(m.radiant_team_name || "The Radiant") + "</span>" +
          '<span class="team__tag">Radiant</span></span>' +
        '<span class="score">' +
          '<span class="' + (rWon ? "win" : "") + '">' + m.radiant_score + "</span>" +
          '<span class="sep">–</span>' +
          '<span class="' + (rWon ? "" : "win") + '">' + m.dire_score + "</span></span>" +
        '<span class="team team--dire team--r' + (rWon ? " is-loser" : "") + '">' +
          '<span class="team__name">' + esc(m.dire_team_name || "The Dire") + "</span>" +
          '<span class="team__tag">Dire</span></span>' +
        '<span class="match__meta"><span>' + esc(dur(m.duration_seconds)) + "</span>" +
          "<span>" + esc(m.played_at ? m.played_at.split(" ")[1] : "—") + "</span>" +
          '<span class="chev" aria-hidden="true"></span></span>';

      var body = el("div", "match__body"), inner = el("div", "match__inner");
      var w1 = el("div", "board-wrap");
      w1.appendChild(board("radiant", m.radiant, m.radiant_team_name || "The Radiant", rWon));
      var w2 = el("div", "board-wrap");
      w2.appendChild(board("dire", m.dire, m.dire_team_name || "The Dire", !rWon));
      inner.appendChild(w1); inner.appendChild(w2);
      if (m.notes) inner.appendChild(el("p", "match__note", esc(m.notes)));
      body.appendChild(inner);

      head.addEventListener("click", function () {
        var open = card.classList.toggle("is-open");
        head.setAttribute("aria-expanded", open ? "true" : "false");
      });
      card.appendChild(head); card.appendChild(body);
      wrap.appendChild(card);
    });
  }

  /* ── Heroes ───────────────────────────────────────────────────── */
  function drawHeroes() {
    var wrap = $("#heroes");
    wrap.innerHTML = "";

    var list = cur.heroes.filter(function (h) {
      return match(state.qHeroes, [h.hero].concat(h.players));
    });
    if (state.heroSort === "win") {
      list = list.slice().sort(function (a, b) {
        // Win rate alone would float a 1-pick 100% above a 4-pick 75%, so
        // ties and near-ties fall back to sample size.
        var wa = a.wins / a.picks, wb = b.wins / b.picks;
        return (wb - wa) || (b.picks - a.picks) || a.hero.localeCompare(b.hero);
      });
    } else if (state.heroSort === "name") {
      list = list.slice().sort(function (a, b) { return a.hero.localeCompare(b.hero); });
    }

    if (!list.length) {
      wrap.innerHTML = '<p class="none">No heroes match that.</p>';
      return;
    }

    list.forEach(function (h, i) {
      var w = Math.round((h.wins / h.picks) * 100);
      var src = heroSrc(h.hero, "art");
      var c = el("button", "hcard");
      c.type = "button";
      c.setAttribute("aria-label", "Who has played " + h.hero);
      c.style.setProperty("--i", i);
      c.innerHTML =
        '<div class="hcard__art">' +
          '<span class="hcard__picks">' + h.picks + (h.picks === 1 ? " pick" : " picks") + "</span>" +
          (src ? '<img src="' + src + '" alt="" loading="lazy" decoding="async"' +
                 ' onerror="this.style.display=\'none\'">' : "") +
        "</div>" +
        '<div class="hcard__body">' +
          '<div class="hcard__name">' + esc(h.hero) + "</div>" +
          '<div class="hcard__row"><span class="hcard__wl"><b>' + h.wins + "W</b> · <i>" +
            h.losses + "L</i></span><span>" + w + "%</span></div>" +
          '<div class="hcard__meter"><span style="width:' + w + '%"></span></div>' +
          '<div class="hcard__who">' + esc(h.players.join(", ")) + "</div>" +
        "</div>";
      c.addEventListener("click", function () { openHeroDrawer(h); });
      wrap.appendChild(c);
    });
  }

  /* ── Duos: with / without ─────────────────────────────────────────
     "Am I better with this person, or is it just that we both play a
     lot?" needs three records side by side, not one.

     WITH    = they were on my team
     WITHOUT = they were not on my team — includes games they sat out
               AND games they were on the enemy side
     AGAINST = they were on the enemy team (a subset of WITHOUT)

     WITHOUT deliberately folds in the enemy games. The question is
     "does having them beside me help?", and the honest comparison is
     every other game I played, not a hand-picked slice of them. AGAINST
     is broken out separately because it answers a different question
     and would otherwise be invisible. */
  function sideOf(m, name) {
    for (var i = 0; i < m.radiant.length; i++) if (m.radiant[i].name === name) return "radiant";
    for (var j = 0; j < m.dire.length; j++)    if (m.dire[j].name === name)    return "dire";
    return null;
  }
  function rec() { return { g: 0, w: 0, l: 0 }; }
  function add(r, won) { r.g++; if (won) r.w++; else r.l++; return r; }
  function pctOf(r) { return r.g ? (r.w / r.g) * 100 : null; }

  // Every teammate/opponent split for one player, in a single pass.
  function partners(name) {
    var out = {};
    function slot(n) {
      if (!out[n]) out[n] = { name: n, with: rec(), without: rec(), against: rec() };
      return out[n];
    }
    var mine = [];
    cur.matches.forEach(function (m) {
      var side = sideOf(m, name);
      if (!side) return;
      var team = side === "radiant" ? m.radiant : m.dire;
      var foe  = side === "radiant" ? m.dire    : m.radiant;
      var won  = m.winning_side === side;
      mine.push({ m: m, won: won, mates: {}, foes: {} });
      var last = mine[mine.length - 1];
      team.forEach(function (r) { if (r.name !== name) last.mates[r.name] = 1; });
      foe.forEach(function (r) { last.foes[r.name] = 1; });
    });
    // Anyone who ever shared a match, on either side, gets a row.
    var seen = {};
    mine.forEach(function (g) {
      Object.keys(g.mates).forEach(function (n) { seen[n] = 1; });
      Object.keys(g.foes).forEach(function (n) { seen[n] = 1; });
    });
    Object.keys(seen).forEach(function (n) {
      var s = slot(n);
      mine.forEach(function (g) {
        if (g.mates[n]) add(s.with, g.won);
        else            add(s.without, g.won);
        if (g.foes[n])  add(s.against, g.won);
      });
    });
    return { rows: Object.keys(out).map(function (n) { return out[n]; }), games: mine.length };
  }

  // Combined view for a selected set: all on one team, or split apart.
  function squad(names) {
    var together = rec(), split = rec(), perSolo = {}, perSplit = {};
    names.forEach(function (n) { perSolo[n] = rec(); perSplit[n] = rec(); });
    cur.matches.forEach(function (m) {
      var sides = names.map(function (n) { return sideOf(m, n); });
      var present = sides.filter(Boolean);
      if (!present.length) return;
      var all = present.length === names.length;
      var same = all && present.every(function (s) { return s === present[0]; });
      var opposed = present.length > 1 &&
                    !present.every(function (s) { return s === present[0]; });
      if (same) {
        add(together, m.winning_side === present[0]);
      } else if (opposed) {
        split.g++;   // they were on opposing sides; nobody "wins" as a group
        // ...but the individuals certainly did, and for two people that is
        // just the head-to-head record — the obvious question to ask.
        names.forEach(function (n, i) {
          if (sides[i]) add(perSplit[n], m.winning_side === sides[i]);
        });
      }
      // Each member's record in games where the full squad was NOT together.
      names.forEach(function (n, i) {
        if (sides[i] && !same) add(perSolo[n], m.winning_side === sides[i]);
      });
    });
    return { together: together, split: split, apart: perSolo, versus: perSplit };
  }

  function statCard(label, r, hint) {
    var p = pctOf(r);
    return '<div class="duo-stat">' +
      '<p class="duo-stat__label">' + esc(label) + "</p>" +
      '<p class="duo-stat__big">' + (p === null ? "—" : p.toFixed(0) + "<i>%</i>") + "</p>" +
      '<p class="duo-stat__sub">' + r.g + (r.g === 1 ? " game · " : " games · ") +
        "<b>" + r.w + "W</b> <i>" + r.l + "L</i></p>" +
      (hint ? '<p class="duo-stat__hint">' + esc(hint) + "</p>" : "") +
      "</div>";
  }

  function drawDuos() {
    var pickWrap = $("#duoPicker"), body = $("#duoBody");
    if (!pickWrap) return;

    // ── the picker ──
    var roster = cur.players.slice().sort(function (a, b) {
      return b.games - a.games || a.name.localeCompare(b.name);
    });
    var q = state.qDuos.toLowerCase().trim();
    pickWrap.innerHTML = "";
    roster.forEach(function (p) {
      var on = state.duoPick.indexOf(p.name) !== -1;
      if (q && !on && hay(p.name).indexOf(q) === -1) return;
      var b = el("button", "chip" + (on ? " is-on" : ""),
        esc(p.name) + '<span class="chip__n">' + p.games + "</span>");
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.addEventListener("click", function () {
        var i = state.duoPick.indexOf(p.name);
        if (i === -1) state.duoPick.push(p.name); else state.duoPick.splice(i, 1);
        drawDuos();
      });
      pickWrap.appendChild(b);
    });

    var sel = state.duoPick.filter(function (n) {
      return cur.players.some(function (p) { return p.name === n; });
    });

    if (!sel.length) {
      body.innerHTML = '<p class="none">Pick a player above to see who they win with — ' +
        "and who they win without. Pick several to check the squad.</p>";
      return;
    }

    var h = "";

    /* ── one player: the full teammate ledger ── */
    if (sel.length === 1) {
      var me = cur.players.filter(function (p) { return p.name === sel[0]; })[0];
      var pr = partners(sel[0]);

      h += '<div class="duo-head">' + faceTag(me.topHero, "duo-head__face") +
        "<div><h3>" + esc(me.name) + "</h3>" +
        "<p>" + me.games + " games · " + me.wins + "W " + me.losses + "L · " +
          pct(me.winPct) + " overall · rating " + me.rating.toFixed(1) + "</p></div></div>";

      var rows = pr.rows.filter(function (r) { return r.with.g >= state.duoMin; });
      rows.sort(function (a, b) {
        if (state.duoSort === "games") return b.with.g - a.with.g || a.name.localeCompare(b.name);
        if (state.duoSort === "name")  return a.name.localeCompare(b.name);
        var da = (pctOf(a.with) || 0) - (pctOf(a.without) === null ? 0 : pctOf(a.without));
        var db = (pctOf(b.with) || 0) - (pctOf(b.without) === null ? 0 : pctOf(b.without));
        return db - da || b.with.g - a.with.g;
      });

      h += '<div class="card table-card"><div class="table-scroll">' +
        '<table class="grid duo-grid"><thead><tr>' +
        '<th class="c-player">Alongside</th>' +
        '<th class="c-num">Together</th><th class="c-num">W–L</th><th class="c-num">With</th>' +
        '<th class="c-num">Without</th><th class="c-num">Swing</th>' +
        '<th class="c-bar">With / without</th>' +
        '<th class="c-num c-opt">Against</th>' +
        "</tr></thead><tbody>";

      if (!rows.length) {
        h += '<tr><td colspan="8" class="none">No one has played ' + state.duoMin +
             "+ games on their team.</td></tr>";
      }
      rows.forEach(function (r) {
        var pw = pctOf(r.with), po = pctOf(r.without), ag = pctOf(r.against);
        var d = (po === null || pw === null) ? null : pw - po;
        h += "<tr>" +
          '<td class="c-player"><span class="who"><span class="who__name">' + esc(r.name) +
            "</span></span></td>" +
          "<td>" + r.with.g + "</td>" +
          '<td class="c-kda"><b class="w-num">' + r.with.w + "</b>–<b class=\"l-num\">" +
            r.with.l + "</b></td>" +
          '<td class="pct">' + (pw === null ? "—" : pw.toFixed(0) + "%") + "</td>" +
          '<td class="dim">' + (po === null ? "—" : po.toFixed(0) + "%") + "</td>" +
          '<td class="' + (d === null ? "dim" : d > 0 ? "w-num" : d < 0 ? "l-num" : "dim") + '">' +
            (d === null ? "—" : (d > 0 ? "+" : "") + d.toFixed(0)) + "</td>" +
          '<td><div class="duo-bars">' +
            '<div class="duo-bar"><span style="width:' + (pw || 0).toFixed(1) + '%"></span></div>' +
            '<div class="duo-bar duo-bar--ghost"><span style="width:' +
              (po === null ? 0 : po).toFixed(1) + '%"></span></div>' +
          "</div></td>" +
          '<td class="c-opt dim">' + (r.against.g ? r.against.w + "–" + r.against.l +
            " · " + (ag === null ? "—" : ag.toFixed(0) + "%") : "—") + "</td>" +
          "</tr>";
      });
      h += "</tbody></table></div></div>" +
        '<p class="note"><b>With</b> is games they were on your team. <b>Without</b> is every ' +
        "other game you played — including ones they were on the enemy side, because the " +
        "honest comparison is your whole record, not a chosen slice of it. " +
        "<b>Swing</b> is the gap between the two, in points.</p>";

    /* ── several players: does the squad hold up together? ── */
    } else {
      var sq = squad(sel);
      h += '<div class="duo-head"><div><h3>' + esc(sel.join("  +  ")) + "</h3>" +
        "<p>" + sel.length + " players selected</p></div></div>";
      /* The second card used to read "Split up — 9 games, neither won nor
         lost by the group", which is true and completely unhelpful: a bare
         count of games with no result attached. For two people those games
         have an obvious meaning — it is the head-to-head — so show that
         instead. For three or more there is no single scoreline, so name
         each player's record and say plainly what the card is counting. */
      var opp;
      if (sel.length === 2) {
        var va = sq.versus[sel[0]], vb = sq.versus[sel[1]];
        opp = '<div class="duo-stat">' +
          '<p class="duo-stat__label">Head to head</p>' +
          '<p class="duo-stat__big">' + va.w + '<i class="vs">&ndash;</i>' + vb.w + "</p>" +
          '<p class="duo-stat__sub">' + esc(sel[0]) + " won " + va.w +
            " &middot; " + esc(sel[1]) + " won " + vb.w + "</p>" +
          '<p class="duo-stat__hint">' + sq.split.g +
            (sq.split.g === 1 ? " game" : " games") + " on opposite teams</p></div>";
      } else {
        opp = '<div class="duo-stat">' +
          '<p class="duo-stat__label">Against each other</p>' +
          '<p class="duo-stat__big">' + sq.split.g +
            "<i>" + (sq.split.g === 1 ? " game" : " games") + "</i></p>" +
          '<p class="duo-stat__sub">split across both teams, so there is ' +
            "no shared result</p>" +
          '<p class="duo-stat__hint">' + sel.map(function (n) {
            return esc(n) + " " + sq.versus[n].w + "&ndash;" + sq.versus[n].l;
          }).join(" &middot; ") + "</p></div>";
      }

      h += '<div class="duo-stats">' +
        statCard("On the same team", sq.together, "all " + sel.length + " together") +
        opp + "</div>";

      h += '<p class="d-sub">Each of them, when the squad was not together</p>' +
        '<div class="card table-card"><div class="table-scroll">' +
        '<table class="grid duo-grid"><thead><tr><th class="c-player">Player</th>' +
        '<th class="c-num">Apart</th><th class="c-num">W–L</th><th class="c-num">Win rate</th>' +
        '<th class="c-num">With squad</th><th class="c-num">Swing</th></tr></thead><tbody>';
      var tp = pctOf(sq.together);
      sel.forEach(function (n) {
        var r = sq.apart[n], p2 = pctOf(r);
        var d = (tp === null || p2 === null) ? null : tp - p2;
        h += "<tr>" +
          '<td class="c-player"><span class="who"><span class="who__name">' + esc(n) + "</span></span></td>" +
          "<td>" + r.g + "</td>" +
          '<td class="c-kda"><b class="w-num">' + r.w + '</b>–<b class="l-num">' + r.l + "</b></td>" +
          '<td class="dim">' + (p2 === null ? "—" : p2.toFixed(0) + "%") + "</td>" +
          '<td class="pct">' + (tp === null ? "—" : tp.toFixed(0) + "%") + "</td>" +
          '<td class="' + (d === null ? "dim" : d > 0 ? "w-num" : d < 0 ? "l-num" : "dim") + '">' +
            (d === null ? "—" : (d > 0 ? "+" : "") + d.toFixed(0)) + "</td></tr>";
      });
      h += "</tbody></table></div></div>" +
        '<p class="note">A squad only has a shared win rate when every selected player was on ' +
        "the <b>same team</b>. Games where they were split across both sides are counted " +
        "separately — one of them won and one of them lost, so the group did neither.</p>";
    }

    body.innerHTML = h;
  }

  /* ── Drawer ───────────────────────────────────────────────────── */
  var drawer = $("#drawer"), scrim = $("#scrim"), lastFocus = null;

  function openDrawer(p) {
    lastFocus = document.activeElement;
    var h =
      '<div class="d-top">' + faceTag(p.topHero, "d-face") +
        '<div><p class="d-eyebrow">' +
          (state.year === "all" ? "All time" : state.year) + "</p>" +
          '<h2 class="d-name" id="drawerName">' + esc(p.name) + "</h2></div></div>" +
      '<dl class="d-stats">' +
        [["Games", p.games], ["Won", p.wins], ["Lost", p.losses],
         ["Win rate", pct(p.winPct)], ["Rating", p.rating.toFixed(1)], ["KDA", rat(p.kda)]]
        .map(function (s) { return "<div><dt>" + esc(s[0]) + "</dt><dd>" + esc(s[1]) + "</dd></div>"; })
        .join("") + "</dl>" +
      '<p class="d-sub">Averages</p><dl class="d-stats">' +
        [["GPM", num(p.avgGpm)], ["XPM", num(p.avgXpm)],
         ["Net worth", num(p.avgNet)], ["Best net", num(p.bestNet)]]
        .map(function (s) { return "<div><dt>" + esc(s[0]) + "</dt><dd>" + esc(s[1]) + "</dd></div>"; })
        .join("") + "</dl>" +
      '<p class="d-sub">Match by match</p>';

    p.history.forEach(function (m) {
      h += '<div class="d-row">' +
        '<span class="d-pill ' + (m.won ? "w" : "l") + '">' + (m.won ? "Win" : "Loss") + "</span>" +
        faceTag(m.hero, "d-face-sm") +
        '<span><span class="d-hero">' + esc(m.hero || "—") + "</span><br>" +
          '<span class="d-when">' + esc(pretty(m.played)) + "</span></span>" +
        '<span class="d-kda">' + m.k + "/" + m.d + "/" + m.a +
          '<br><span class="d-when">' + num(m.net) + " net</span></span></div>";
    });

    if (p.heroes.length > 1) {
      h += '<p class="d-sub">Most played</p>';
      p.heroes.forEach(function (x) {
        h += '<div class="d-row"><span class="d-pill w">' + x.picks + "</span>" +
             faceTag(x.hero, "d-face-sm") +
             '<span class="d-hero">' + esc(x.hero) + "</span>" +
             '<span class="d-kda">' + (x.picks === 1 ? "once" : x.picks + "×") + "</span></div>";
      });
    }

    $("#drawerBody").innerHTML = h;
    drawer.hidden = false; scrim.hidden = false;
    document.body.style.overflow = "hidden";
    $("#drawerClose").focus();
  }

  // Who has played this hero, how often, and how it went for each of them.
  function openHeroDrawer(h) {
    lastFocus = document.activeElement;
    var w = Math.round((h.wins / h.picks) * 100);
    var art = heroSrc(h.hero, "art");
    var s =
      '<div class="d-hero-art">' +
        (art ? '<img src="' + art + '" alt="" decoding="async" onerror="this.remove()">' : "") +
        '<div class="d-hero-cap"><p class="d-eyebrow">' +
          (state.year === "all" ? "All time" : state.year) + "</p>" +
          '<h2 class="d-name" id="drawerName">' + esc(h.hero) + "</h2></div></div>" +
      '<dl class="d-stats">' +
        [["Picks", h.picks], ["Won", h.wins], ["Lost", h.losses],
         ["Win rate", w + "%"], ["Players", h.roster.length]]
        .map(function (x) { return "<div><dt>" + esc(x[0]) + "</dt><dd>" + esc(x[1]) + "</dd></div>"; })
        .join("") + "</dl>" +
      '<p class="d-sub">Picked by</p>';

    h.roster.forEach(function (q) {
      var qw = Math.round((q.wins / q.picks) * 100);
      s += '<div class="d-row d-row--hero">' +
        '<span class="d-pill ' + (q.wins >= q.losses ? "w" : "l") + '">' + q.picks + "&times;</span>" +
        '<span><span class="d-hero">' + esc(q.name) + "</span><br>" +
          '<span class="d-when">' + q.wins + "W · " + q.losses + "L</span></span>" +
        '<span class="d-mini"><span style="width:' + qw + '%"></span></span>' +
        '<span class="d-kda">' + qw + "%</span></div>";
    });

    $("#drawerBody").innerHTML = s;
    drawer.hidden = false; scrim.hidden = false;
    document.body.style.overflow = "hidden";
    $("#drawerClose").focus();
  }

  function closeDrawer() {
    drawer.hidden = true; scrim.hidden = true;
    document.body.style.overflow = "";
    if (lastFocus) lastFocus.focus();
  }

  /* ── Tabs ─────────────────────────────────────────────────────────
     The tab lives in location.hash so a view can be linked to — "look
     at #duos=HURR%20%5BPK_%5D" is the natural way to share a finding
     with the person it is about, and it costs one line to support. */
  function showTab(name) {
    var view = $("#view-" + name);
    if (!view) return false;
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (o) {
      var on = o.getAttribute("data-view") === name;
      o.classList.toggle("is-active", on);
      o.setAttribute("aria-selected", on ? "true" : "false");
    });
    Array.prototype.forEach.call(document.querySelectorAll(".view"), function (v) {
      v.classList.remove("is-active");
    });
    view.classList.add("is-active");
    // Measured layout: the bracket's connector lines cannot be drawn while
    // its tab is hidden, because every box rect is 0x0 there.
    if (name === "mini") miLines();
    return true;
  }

  function fromHash() {
    var raw = decodeURIComponent(String(location.hash || "").replace(/^#/, ""));
    if (!raw) return;
    var eq = raw.indexOf("="), name = eq === -1 ? raw : raw.slice(0, eq);
    if (eq !== -1 && name === "duos") {
      // Names can contain commas? None do, but split on the pipe instead
      // so a future "Fear, Inc" cannot silently become two players.
      state.duoPick = raw.slice(eq + 1).split("|").filter(Boolean);
    }
    if (showTab(name)) drawDuos();
  }

  function wireTabs() {
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
      t.addEventListener("click", function () {
        var name = t.getAttribute("data-view");
        showTab(name);
        var frag = name === "duos" && state.duoPick.length
          ? "duos=" + state.duoPick.map(encodeURIComponent).join("|") : name;
        if (history.replaceState) history.replaceState(null, "", "#" + frag);
        else location.hash = frag;
      });
    });
    window.addEventListener("hashchange", fromHash);
  }

  /* ── Teams (league) ───────────────────────────────────────────────
     The Teams tab shows the four fixed rosters and a team-level
     standings table using the SAME Wilson rating as the individual
     board — win-rate lower bound, so 5-0 does not beat 12-2. */

  var LEAGUE = D.league || null;

  /* A match counts towards a team's record ONLY if it was explicitly
     recorded against a scheduled series. It used to be *inferred* -- any
     match after the season start where 3+ players on one side shared a
     team was booked as a league result. That is wrong here: these people
     play inhouse together every night, so a casual pub game whose sides
     happened to line up became a permanent win. It put Team 3 on 1-0 and
     Team 1 on 0-1 before a single league match had been played.

     Guessing is exactly the failure this project cannot have, so team
     records now come only from results tied to a fixture. Until results
     are recorded, every team correctly reads 0. */
  /* The league's own match ledger (data/league_matches.json), exported
     separately from the lobby database. See build_tournament in
     export_web.py. `cur.matches` NEVER contains any of these. */
  var TOUR = (D.tournament && D.tournament.matches) ? D.tournament.matches : [];

  /* League points. 3 for taking a best-of-three, 1 for every individual
     game won.

     Worth knowing: the FINAL ordering is identical whether the 3 is on
     top of the game points or instead of them, because the winner of a
     best-of-three has always won exactly two games — so every series
     winner scores the same either way. The two only differ while a
     series is still in progress, and counting each game as it lands is
     the reading that keeps a half-played series honest. */
  var POINTS = { series: 3, game: 1 };

  function aggregateTeams() {
    if (!LEAGUE) return null;
    var T = {};
    LEAGUE.teams.forEach(function (t) {
      T[t.id] = { id: t.id, name: t.name, roster: t.roster,
                  games: 0, wins: 0, losses: 0,
                  seriesWins: 0, seriesLosses: 0, seriesPlayed: 0, points: 0,
                  opponents: {} };
    });

    // Series are only decided on the fixture list, so they are counted
    // from there rather than from the match ledger.
    allSeries().forEach(function (e) {
      var s = e.s;
      if (s.status !== "final" || !s.teams || s.teams.length !== 2) return;
      var a = s.teams[0], b = s.teams[1], sc = s.score || [0, 0];
      if (!T[a] || !T[b] || sc[0] === sc[1]) return;
      var win = sc[0] > sc[1] ? a : b, lose = win === a ? b : a;
      T[win].seriesWins++; T[lose].seriesLosses++;
      T[a].seriesPlayed++; T[b].seriesPlayed++;
    });

    /* Team records now come from the LEAGUE LEDGER, not from matches
       linked to a fixture. That is a change, and it is safe for a reason
       that did not hold before: a match cannot enter that ledger unless
       every player on each side is on the same team's roster and the two
       sides are different teams (tools/league_ingest.py enforces it on
       the way in). So being in the ledger IS the explicit signal.

       The old rule existed because team records were once INFERRED from
       any lobby match whose sides happened to line up, which put Team 3
       on 1-0 before a league game had been played. Nothing is inferred
       here either — a pub game cannot reach this array at all. */
    TOUR.forEach(function (m) {
      var a = m.radiant_team_id, b = m.dire_team_id, win = m.winner_team_id;
      if (!T[a] || !T[b] || (win !== a && win !== b) || a === b) return;
      var lose = win === a ? b : a;
      T[a].games++; T[b].games++;
      T[win].wins++; T[lose].losses++;
      T[a].opponents[b] = (T[a].opponents[b] || 0) + 1;
      T[b].opponents[a] = (T[b].opponents[a] || 0) + 1;
    });

    // Compute pace, rating, status.
    var teams = LEAGUE.teams.map(function (t) { return T[t.id]; });
    teams.forEach(function (t) {
      t.points = POINTS.series * t.seriesWins + POINTS.game * t.wins;
      t.winPct = t.games ? (t.wins / t.games) * 100 : 0;
      t.rating = wilson(t.wins, t.games, state.evidence) * SCALE;
      // Pace: games per opponent slot. Always /3, not /distinct-faced
      // (which would perversely reward playing only one opponent).
      t.pace = t.games / 3;
    });

    // League median pace and qualification floor.
    var paces = teams.map(function (t) { return t.pace; }).sort(function (a, b) { return a - b; });
    var median = paces.length
      ? (paces.length % 2
          ? paces[(paces.length - 1) / 2]
          : (paces[paces.length / 2 - 1] + paces[paces.length / 2]) / 2)
      : 0;
    var floorPct = (LEAGUE.season && LEAGUE.season.qualification_floor_pct) || 0.80;
    var floor = median * floorPct;

    teams.forEach(function (t) {
      var r = median ? t.pace / median : 1;
      var tier;
      if (t.pace === 0 && median === 0) tier = "empty";       // no league games yet
      else if (r >= 1.00)               tier = "green";
      else if (r >= 0.80)               tier = "yellow";
      else if (r >= 0.70)               tier = "orange";
      else                              tier = "red";
      t.tier = tier;
      t.eligible = t.pace >= floor;
    });

    // Rank: rating desc, then wins desc, then name asc.
    teams.sort(function (a, b) {
      return (b.rating - a.rating) || (b.wins - a.wins)
          || a.name.localeCompare(b.name);
    });

    return { teams: teams, median: median, floor: floor,
             totalMatches: teams.reduce(function (s, t) { return s + t.games; }, 0) / 2 };
  }

  var TIER_LABEL = { green: "On pace", yellow: "Below median",
                     orange: "Warning", red: "At risk", empty: "—" };

  function drawLeagueHud(agg) {
    var wrap = $("#leagueHud");
    if (!wrap) return;
    if (!LEAGUE) { wrap.innerHTML = ""; return; }
    var s = LEAGUE.season || {};
    var end = s.end_at ? new Date(s.end_at) : null;
    var now = new Date();
    var daysLeft = end ? Math.max(0, Math.ceil((end - now) / 86400000)) : null;
    var prize = s.prize_usd != null ? "$" + s.prize_usd.toLocaleString("en-US") : "";
    var totalMatches = agg ? agg.totalMatches : 0;

    wrap.innerHTML =
      '<div class="hud card">' +
        '<div class="hud__cell">' +
          '<div class="hud__label">Season</div>' +
          '<div class="hud__value">' + esc(s.name || "—") + '</div>' +
        '</div>' +
        '<div class="hud__cell">' +
          '<div class="hud__label">Days left</div>' +
          '<div class="hud__value">' + (daysLeft != null ? daysLeft : "—") + '</div>' +
        '</div>' +
        '<div class="hud__cell">' +
          '<div class="hud__label">League matches</div>' +
          '<div class="hud__value">' + totalMatches + '</div>' +
        '</div>' +
        '<div class="hud__cell hud__cell--prize">' +
          '<div class="hud__label">Prize</div>' +
          '<div class="hud__value">' + esc(prize) + '</div>' +
        '</div>' +
      '</div>';
  }

  function drawTeamStandings(agg) {
    var tb = $("#teamStandings tbody");
    if (!tb) return;
    tb.innerHTML = "";
    if (!agg) {
      tb.innerHTML = '<tr><td colspan="10" class="none">' +
        'League roster not loaded. Add data/teams.json and re-export.</td></tr>';
      return;
    }
    agg.teams.forEach(function (t, i) {
      var tr = el("tr");
      tr.style.setProperty("--i", i);
      if (i === 0 && t.games > 0) tr.classList.add("is-lead");
      if (!t.eligible && agg.floor > 0) tr.classList.add("is-inelig");
      tr.innerHTML =
        '<td><span class="rank">' + (i + 1) + "</span></td>" +
        '<td class="c-player"><span class="who">' +
          '<span class="team-chip team-chip--' + t.id + '">' + t.id + '</span>' +
          '<span><span class="who__name">' + esc(t.name) + "</span>" +
          // Starters, not roster size. The lineup card shows five slots and
          // no longer lists stand-ins, so "7 players" beside a five-slot
          // card was two different counts of the same team.
          '<span class="who__hero">' + t.roster.filter(function (r) {
            return r.role !== "stand_in"; }).length + " of 5</span>" +
          "</span></span></td>" +
        "<td>" + t.games + "</td>" +
        '<td class="w-num">' + t.wins + "</td>" +
        '<td class="l-num">' + t.losses + "</td>" +
        '<td><div class="meter"><span style="width:' + t.winPct.toFixed(1) + '%"></span></div></td>' +
        '<td class="pct">' + (t.games ? pct(t.winPct) : "—") + "</td>" +
        '<td>' + (t.games ? t.pace.toFixed(1) : "—") + "</td>" +
        '<td class="tier tier--' + t.tier + '" title="' + esc(TIER_LABEL[t.tier]) + '">' +
          '<span class="tier-dot"></span></td>' +
        '<td class="c-rating"><span class="rating' + ratingTier(t.rating) + '">' +
          (t.games ? t.rating.toFixed(1) : "—") + "</span></td>";
      tb.appendChild(tr);
    });
  }

  /* ── Rosters: a LINEUP SHEET, not a list ─────────────────────────
     Every card runs the same five slots in the same order, so the five
     cards read across as one grid — mid against mid, carry against carry.
     That is the whole reason the position rail is a fixed column rather
     than a label per row: alignment is what makes a lineup legible.

     A slot with nobody in it stays a slot. Team 2 has no carry on the
     captains' sheet, and collapsing that row would hide a real gap —
     the card would silently read as a complete team of four. */

  var SLOTS = [
    { key: "mid",     label: "Mid" },
    { key: "carry",   label: "Carry" },
    { key: "offlane", label: "Offlane" },
    { key: "support", label: "Support" },
    { key: "support", label: "Support" }
  ];

  function friendly(name, roster) {
    var hit = (roster || []).filter(function (x) { return x.name === name; })[0];
    return (hit && hit.aka && hit.aka.length) ? hit.aka[0] : name;
  }

  /* Fill the five-slot template from a team's starters. Extra players in a
     position spill into the next free slot rather than vanishing, and a
     position nobody plays leaves its slot empty. */
  function lineup(team) {
    var pool = team.roster.filter(function (r) { return r.role !== "stand_in"; });
    var out = SLOTS.map(function (s) { return { slot: s, player: null }; });
    var taken = [];
    out.forEach(function (cell) {
      for (var i = 0; i < pool.length; i++) {
        if (taken.indexOf(i) >= 0) continue;
        if (pool[i].position === cell.slot.key) { cell.player = pool[i]; taken.push(i); return; }
      }
    });
    pool.forEach(function (r, i) {            // anyone with an odd position
      if (taken.indexOf(i) >= 0) return;
      for (var j = 0; j < out.length; j++) {
        if (!out[j].player) { out[j].player = r; taken.push(i); return; }
      }
    });
    return out;
  }

  function tierBadge(tier) {
    if (tier == null) return "";
    return '<span class="tier tier--' + esc(tier) + '" title="Draft tier ' +
      esc(tier) + '">' + (tier === "legend" ? "L" : esc(tier)) + '</span>';
  }

  function drawRosterGrid() {
    var wrap = $("#teamsGrid");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!LEAGUE) return;

    LEAGUE.teams.forEach(function (t, ti) {
      var rows = lineup(t);
      var filled = rows.filter(function (r) { return r.player; }).length;

      var card = el("div", "lineup card");
      card.style.setProperty("--i", ti);          // staggers the reveal

      var head = el("div", "lineup__head");
      head.innerHTML =
        '<span class="team-chip team-chip--' + t.id + '">' + t.id + '</span>' +
        '<span class="lineup__name">' + esc(t.name) + '</span>' +
        '<span class="lineup__count' + (filled < rows.length ? " is-short" : "") +
          '">' + filled + '<span class="lineup__of">/' + rows.length + '</span></span>';
      card.appendChild(head);

      var body = el("div", "lineup__body");
      rows.forEach(function (cell) {
        var li = el("div", "lu" + (cell.player ? "" : " is-empty"));
        var pos = '<span class="lu__pos">' + esc(cell.slot.label) + '</span>';
        if (!cell.player) {
          li.innerHTML = pos + '<span class="lu__open">Open</span>';
          body.appendChild(li);
          return;
        }
        var r = cell.player;
        var display = (r.aka && r.aka.length) ? r.aka[0] : r.name;
        var backs = r.backup == null ? [] : (r.backup.push ? r.backup : [r.backup]);
        // Canonical name and substitute share ONE line. Every filled row
        // is then exactly two lines tall, which is what lets the five
        // cards line up row-for-row -- the reason the rail exists.
        var meta = [];
        if (display !== r.name) {
          meta.push('<span class="lu__canon">' + esc(r.name) + '</span>');
        }
        if (backs.length) {
          meta.push('<span class="lu__sub" title="Can be filled by">' +
            backs.map(function (b) { return esc(friendly(b, t.roster)); })
                 .join(", ") + '</span>');
        }
        li.innerHTML = pos +
          '<span class="lu__who">' +
            '<span class="lu__name">' + esc(display) + '</span>' +
            (meta.length ? '<span class="lu__meta">' + meta.join(
               '<span class="lu__dot">·</span>') + '</span>' : "") +
          '</span>' + tierBadge(r.tier);
        body.appendChild(li);
      });
      card.appendChild(body);
      wrap.appendChild(card);
    });
  }

  /* ── The draft, as a table ───────────────────────────────────────
     Built by joining the tier pools to the rosters, so it answers the
     question the pools alone cannot: where did each pick end up. A name
     in a pool that is on nobody's roster reads "—", which is how an
     undrafted player shows rather than quietly disappearing. */

  function drawTierTable() {
    var host = $("#tierTable");
    if (!host) return;
    var tiers = (LEAGUE && LEAGUE.tiers) || [];
    if (!tiers.length) { host.innerHTML = ""; return; }

    var where = {};                              // nickname/name -> roster row
    LEAGUE.teams.forEach(function (t) {
      t.roster.forEach(function (r) {
        var keys = [r.name].concat(r.aka || []);
        keys.forEach(function (k) {
          if (!where[k.toLowerCase()]) where[k.toLowerCase()] = { team: t, row: r };
        });
      });
    });

    var body = tiers.map(function (tier) {
      var head = '<tr class="tt-group"><th colspan="4">' + esc(tier.label) +
        '<span class="tt-group__n">' + tier.players.length + '</span></th></tr>';
      var rows = tier.players.map(function (nick) {
        var hit = where[nick.toLowerCase()];
        var pos = hit && hit.row.position ? hit.row.position : "";
        var bench = hit && hit.row.role === "stand_in";
        // The chip and the name are ONE flex box. They used to be siblings,
        // so the gap on .tt-team applied to nothing and the cell read
        // "4gillu_&_co".
        var team = hit
          ? '<span class="tt-team">' +
              '<span class="team-chip team-chip--' + hit.team.id + '">' +
              hit.team.id + '</span>' +
              '<span>' + esc(hit.team.name) +
                (bench ? ' <em>stand-in</em>' : "") + '</span>' +
            '</span>'
          : '<span class="tt-none">—</span>';
        return '<tr' + (bench ? ' class="is-bench"' : "") + '>' +
          '<td class="tt-player">' + esc(nick) + '</td>' +
          '<td class="tt-tier">' + tierBadge(tier.tier) + '</td>' +
          '<td class="tt-pos">' + esc(pos) + '</td>' +
          '<td class="tt-where">' + team + '</td>' +
        '</tr>';
      }).join("");
      return head + rows;
    }).join("");

    host.innerHTML =
      '<div class="card table-card">' +
        '<div class="table-scroll">' +
          '<table class="grid tt">' +
            '<thead><tr>' +
              '<th class="tt-player">Player</th>' +
              '<th class="tt-tier">Tier</th>' +
              '<th class="tt-pos">Position</th>' +
              '<th class="tt-where">Drafted to</th>' +
            '</tr></thead>' +
            '<tbody>' + body + '</tbody>' +
          '</table>' +
        '</div>' +
      '</div>';
  }


  function drawTeams() {
    teamsLede();
    if (!LEAGUE) {
      drawTeamStandings(null);
      drawRosterGrid();
      drawTierTable();          // clears itself when there are no tiers
      drawLeagueHud(null);
      return;
    }
    var agg = aggregateTeams();
    drawLeagueHud(agg);
    drawTeamStandings(agg);
    drawRosterGrid();
    drawTierTable();
  }

  /* ── Coord (scheduling) ─────────────────────────────────────────
     Read-only display of confirmed upcoming matches + open scheduling
     rounds. Input happens in Discord (#dota-league-2026); this tab
     renders whatever the last export knew about.

     All the DST-safe UTC math ran in Python at export time (see
     export_web.py::build_coord). The browser just formats what it's
     given. This keeps zoneinfo out of the browser -- Intl.DateTimeFormat
     has patchy IANA support in older mobile browsers. */

  /* ── Schedule (season fixtures) ───────────────────────────────────
     The whole season, week by week. Every series arrives from
     export_web.py already rendered into each league timezone (see
     build_fixtures), so switching country here is a lookup, not a
     conversion -- the browser never does timezone maths.

     Times are 12-hour throughout. The league reads this tab to decide
     when to show up, and "23:00" is one mental conversion away from
     turning up twelve hours late. */

  var FX = D.fixtures || null;

  var DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  var DAY_FULL = {
    Mon: "Monday", Tue: "Tuesday", Wed: "Wednesday", Thu: "Thursday",
    Fri: "Friday", Sat: "Saturday", Sun: "Sunday"
  };

  function dayFull(d) { return DAY_FULL[d] || d; }
  function nextDay(d) {
    var i = DAY_ORDER.indexOf(d);
    return i < 0 ? "the next day" : dayFull(DAY_ORDER[(i + 1) % 7]);
  }
  function listWords(a) {
    if (!a.length) return "";
    if (a.length === 1) return a[0];
    return a.slice(0, -1).join(", ") + " and " + a[a.length - 1];
  }

  /* The Schedule blurb is written FROM the payload, never typed into the
     HTML. It used to say "Saturday and Sunday" and "each team takes the
     late slot exactly half its nights" — both went false the instant
     make_fixtures.py was re-run onto Friday nights with an odd number of
     cycles, and prose has no checksum to catch it. Same rule as the
     ledger: say what the data says, not what was true when it was typed. */
  /* "Four teams playing continuously..." was typed into the HTML and went
     false the day a fifth team was added -- the same failure as the old
     Schedule copy. The count comes from the roster now. */
  var COUNT_WORD = ["no", "one", "two", "three", "four", "five", "six",
                    "seven", "eight", "nine", "ten"];
  function teamsLede() {
    var el = $("#teamsLede");
    if (!el || !D.league || !D.league.teams) return;
    var n = D.league.teams.length;
    el.innerHTML = el.innerHTML.replace(
      /^[A-Z][a-z]+ teams/,
      (COUNT_WORD[n] || n).replace(/^./, function (c) { return c.toUpperCase(); }) +
      " teams");
  }

  /* The season is a list of NIGHTS, not a list of weeks. Week numbering
     was removed on purpose: nobody in the league thinks in "week 3", and
     a week header implies both its nights get played together, which is
     exactly what does not happen -- teams turn up when they turn up. The
     date is the only part of a fixture that has ever been useful. */
  function allNights() {
    var out = [];
    if (!FX || !FX.weeks) return out;
    FX.weeks.forEach(function (w) {
      w.nights.forEach(function (n) { out.push(n); });
    });
    return out;
  }

  function anyBye() {
    return allNights().some(function (n) { return n.bye && n.bye.length; });
  }

  function fxCopy() {
    if (!FX || !FX.season) return;
    var s = FX.season, t = FX.totals || {};
    var days = (s.night_days || []).map(dayFull);
    var lede = $("#fxLede");
    if (lede && days.length) {
      lede.innerHTML =
        (s.final
          ? "A double round robin, then a <b>best-of-five final</b> between " +
            "the top two. "
          : "The season runs straight through — no playoffs, no final. ") +
        "Two " +
        "<b>best-of-three</b> matches a night, " + listWords(days) + ": one pair " +
        "of teams plays the early slot, the other the late one, so whoever " +
        "isn't playing can watch. <b>" + s.nights + " nights</b> — every team plays <b>" +
        (t.series_per_team || 0) + " best-of-threes</b> and meets every other " +
        "team " + ((t.meetings_per_pair || 0) === 1
          ? "<b>once</b>" : "<b>" + t.meetings_per_pair + " times</b>") + ". " +
        (anyBye() ? "With <b>" + (s.teams || 5) + " teams</b> and two slots one " +
                    "team is off each night — the <b>bye</b>. " : "") +
        "Pick your country to see every time in your own clock.";
    }
    var note = $("#fxNote");
    if (note) {
      var late = t.late_slots_per_team || {};
      var ids = Object.keys(late);
      var vals = ids.map(function (k) { return late[k]; });
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
      var fair = lo === hi
        ? "each team takes the late slot <b>exactly half</b> its nights (" +
          lo + " each)"
        : "the late slot cannot split exactly across an odd number of cycles — " +
          listWords(ids.filter(function (k) { return late[k] === lo; })
                       .map(function (k) { return "Team " + k; })) +
          " takes it <b>" + lo + "</b> times, everyone else <b>" + hi + "</b>";
      var d0 = (s.night_days || [])[0];
      note.innerHTML =
        "The late slot starts at <b>3:00 AM in Pakistan</b>, which is the next " +
        "morning" + (d0 ? " — a " + dayFull(d0) + "-night late match is really " +
        nextDay(d0) + " at 3 AM" : "") + ". Every pair of teams meets the " +
        "<b>same number of times</b>, and " + fair + ". Times are worked out with " +
        "full daylight-saving awareness for each country.";
    }
  }

  function fxZones() {
    var first = FX && FX.weeks[0] && FX.weeks[0].nights[0].series[0];
    return (first && first.local) ? first.local.map(function (l) { return l.label; }) : [];
  }

  function drawZonePicker() {
    var host = $("#fxZone");
    if (!host || !FX) return;
    var zones = fxZones();
    if (!zones.length) return;
    host.innerHTML = '<span class="seg-label">Times in</span>' +
      zones.map(function (z) {
        return '<button class="seg' + (z === state.fxZone ? " is-on" : "") +
               '" data-zone="' + esc(z) + '">' + esc(z) + "</button>";
      }).join("");
    Array.prototype.forEach.call(host.querySelectorAll(".seg"), function (b) {
      b.addEventListener("click", function () {
        state.fxZone = b.getAttribute("data-zone");
        drawZonePicker();
        drawSchedule();
      });
    });
  }

  function fxWindow(s) {
    var hit = (s.local || []).filter(function (l) { return l.label === state.fxZone; })[0];
    return hit ? hit.window : s.pkt_window;
  }

  function fxTeam(id) {
    var t = ((D.league && D.league.teams) || []).filter(function (x) { return x.id === id; })[0];
    return t ? t.name : "Team " + id;
  }

  /* One button per team, carrying the whole name — not a numbered dot
     next to the same words in plain text, which said everything twice. */
  function renderSeries(s) {
    var slotCls = s.slot === 2 ? " is-late" : "";
    // NOT `s.teams ?` -- an empty array is truthy in JS, so the final would
    // have rendered as "team-pill--undefined vs team-pill--undefined".
    var who = !isTBD(s)
      ? '<span class="team-pill team-pill--' + s.teams[0] + '">' +
          esc(fxTeam(s.teams[0])) + '</span>' +
        '<span class="fx-vs">vs</span>' +
        '<span class="team-pill team-pill--' + s.teams[1] + '">' +
          esc(fxTeam(s.teams[1])) + '</span>'
      : '<span class="fx-tbd">' + esc(s.decided_by || s.label ||
                                       "To be decided") + '</span>';
    var bo = s.best_of === 5 ? "best of 5" : "best of 3";
    return '<div class="fx-series' + slotCls + '">' +
      '<span class="fx-slot' + slotCls + '">' + (s.slot === 2 ? "Late" : "Early") + '</span>' +
      '<span class="fx-when">' + esc(fxWindow(s)) + '</span>' +
      '<span class="fx-teams">' + who + '</span>' +
      '<span class="fx-bo">' + bo + '</span>' +
    '</div>';
  }

  function dayLabel(iso) {
    var d = iso.split("-");
    return Number(d[2]) + " " + MON[Number(d[1]) - 1];
  }

  // The next night: the first that has not yet passed. Everything before
  // it is done, so both layouts can mark it and scroll to it.
  function currentNight() {
    var today = new Date().toISOString().slice(0, 10);
    var hit = allNights().filter(function (n) { return n.date >= today; })[0];
    return hit ? hit.date : null;
  }

  /* Timeline: one column per week, scrolling sideways, the way a
     tournament bracket reads. Twenty-one weeks stacked vertically is a
     very long page in which every week looks the same; side by side, the
     shape of the season is visible at a glance and the current week can
     be scrolled to. */
  /* One match = one box with the two teams stacked, the way a tournament
     bracket draws it. A flat "A vs B" line reads as a list of text; a
     stacked pair reads as a fixture, and leaves an obvious place for each
     team's series score to land once results are recorded. */
  function renderMatchBox(s) {
    if (isTBD(s)) return tbdBox(s);
    var late = s.slot === 2;
    var sc = s.score || [0, 0];
    var played = (s.games || []).length > 0;
    function row(i) {
      var tid = s.teams[i];
      var won = played && sc[i] > sc[1 - i];
      return '<div class="mb-row' + (won ? " is-won" : "") + '">' +
        '<span class="team-chip team-chip--' + tid + '">' + tid + '</span>' +
        '<span class="mb-team">' + esc(fxTeam(tid)) + '</span>' +
        '<span class="mb-score">' + (played ? sc[i] : "–") + '</span>' +
      '</div>';
    }
    return '<div class="mb' + (late ? " is-late" : "") + '">' +
      '<div class="mb-when">' +
        '<span class="mb-slot">' + (late ? "Late" : "Early") + '</span>' +
        esc(fxWindow(s)) +
      '</div>' +
      row(0) + row(1) +
    '</div>';
  }

  /* With five teams and two slots, one team is off every night. That is
     new information, not a gap in the data -- so it is drawn, quietly, at
     the foot of the night. A night with nobody off (four teams) renders
     nothing, so the four-team layout is untouched. */
  /* The final is the one series whose teams are unknown when the schedule
     is written. It renders as a labelled placeholder rather than two empty
     rows -- an empty fixture reads as missing data, which it is not. */
  function isTBD(s) { return !s.teams || !s.teams.length; }

  function tbdBox(s) {
    return '<div class="mb is-final">' +
      '<div class="mb-when">' +
        '<span class="mb-slot mb-slot--final">Final</span>' +
        'Best of ' + (s.best_of || 5) + ' · ' + esc(fxWindow(s)) +
      '</div>' +
      '<div class="mb-tbd">' + esc(s.decided_by || "To be decided") + '</div>' +
    '</div>';
  }

  function byeLine(n) {
    if (!n.bye || !n.bye.length) return "";
    return '<div class="tl-bye">' +
      '<span class="tl-bye__lbl">Bye</span>' +
      n.bye.map(function (t) {
        return '<span class="team-chip team-chip--' + t + '">' + t + '</span>';
      }).join("") + '</div>';
  }

  function drawTimeline(host, cur) {
    host.innerHTML = '<div class="tl-rail" id="tlRail">' +
      allNights().map(function (n) {
        var isCur = n.date === cur;
        var done = cur !== null && n.date < cur;
        return '<div class="tl-col' + (isCur ? " is-current" : "") +
                 (done ? " is-done" : "") + '"' +
                 (isCur ? ' id="tlNow"' : "") + '>' +
          '<div class="tl-col__head">' +
            '<span class="tl-col__dow">' + esc(n.day) + '</span>' +
            '<span class="tl-col__n">' + esc(dayLabel(n.date)) + '</span>' +
            (isCur ? '<span class="tl-col__now">Next</span>' : '') +
          '</div>' +
          '<div class="tl-col__body">' +
            n.series.map(renderMatchBox).join("") + byeLine(n) +
          '</div>' +
        '</div>';
      }).join("") +
    '</div>';

    // Bring the next night into view without yanking the whole page.
    var rail = $("#tlRail"), now = $("#tlNow");
    if (rail && now) rail.scrollLeft = Math.max(0, now.offsetLeft - rail.offsetLeft - 16);
  }

  function drawList(host, cur) {
    host.innerHTML = '<div class="fx-list">' +
      allNights().map(function (n, i) {
        var isCur = n.date === cur;
        var done = cur !== null && n.date < cur;
        return '<div class="fx-night' + (i % 2 ? " is-alt" : "") +
                 (isCur ? " is-current" : "") + (done ? " is-done" : "") + '">' +
          '<div class="fx-night__day">' +
            '<span class="fx-night__dow">' + esc(n.day) + '</span>' +
            '<span class="fx-night__date">' + esc(dayLabel(n.date)) + '</span>' +
            (isCur ? '<span class="fx-night__now">Next</span>' : '') +
          '</div>' +
          '<div class="fx-night__body">' +
            n.series.map(renderSeries).join("") + byeLine(n) +
          '</div>' +
        '</div>';
      }).join("") +
    '</div>';
  }

  /* Which matches have been PLAYED.
     It began as a five-by-five "who owes whom" grid, which answered a
     question nobody asks -- how many series does this PAIR still owe --
     and made finding one result a matrix-reading exercise. Then it listed
     every fixture, played or not, which at two meetings a pair is twenty
     rows of "still to play" burying the three that happened.

     So this table is the RESULTS only. What is still to come is the
     schedule underneath it, which is the thing built to show it; the
     count of what is left stays in the header, where one number does the
     job of twenty rows. */
  function spMatch(s) {
    if (isTBD(s)) {
      return '<span class="sp-tbd">' + esc(s.decided_by || s.label || "The top two") + '</span>';
    }
    return '<span class="team-pill team-pill--' + s.teams[0] + '">' +
             esc(fxTeam(s.teams[0])) + '</span>' +
           '<span class="sp-vs">vs</span>' +
           '<span class="team-pill team-pill--' + s.teams[1] + '">' +
             esc(fxTeam(s.teams[1])) + '</span>';
  }

  function spResult(s) {
    var games = s.games || [];
    if (!games.length || !s.teams || s.teams.length !== 2) {
      return { cls: "is-open",
               html: '<span class="sp-badge">Still to play</span>' };
    }
    var sc = s.score || [0, 0];
    var lead = sc[0] >= sc[1] ? 0 : 1;
    var tid = s.teams[lead];
    var line = Math.max(sc[0], sc[1]) + '–' + Math.min(sc[0], sc[1]);
    if (s.status === "final") {
      return { cls: "is-done", html:
        '<span class="sp-won">' +
          '<span class="team-chip team-chip--' + tid + '">' + tid + '</span>' +
          esc(fxTeam(tid)) + ' won</span>' +
        '<span class="sp-sc">' + line + '</span>' };
    }
    return { cls: "is-live", html:
      '<span class="sp-badge is-live">In progress</span>' +
      '<span class="sp-sc">' + (sc[0] === sc[1]
        ? line
        : esc(fxTeam(tid)) + ' leads ' + line) + '</span>' };
  }

  function drawProgress() {
    var host = $("#seasonProgress");
    if (!host) return;
    if (!FX || !FX.weeks || !FX.weeks.length) { host.innerHTML = ""; return; }

    var all = allSeries();
    var done = 0, live = 0;
    var rows = all.map(function (e) {
      var r = spResult(e.s);
      if (r.cls === "is-done") done++;
      else if (r.cls === "is-live") live++;
      return { e: e, r: r };
    }).filter(function (x) { return x.r.cls !== "is-open"; });

    var body = rows.map(function (x) {
      return '<tr class="' + x.r.cls + '">' +
        '<td class="sp-td-m"><span class="sp-m">' + spMatch(x.e.s) + '</span></td>' +
        '<td class="sp-when">' + esc(x.e.n.day) + ' ' + esc(dayLabel(x.e.n.date)) +
          '<span class="sp-slot' + (x.e.s.slot === 2 ? " is-late" : "") + '">' +
            (x.e.s.slot === 2 ? "Late" : "Early") + '</span></td>' +
        '<td class="sp-td-r"><span class="sp-res">' + x.r.html + '</span></td>' +
      '</tr>';
    }).join("");

    var total = all.length;
    var left = total - done - live;
    var pct = total ? Math.round((done / total) * 100) : 0;
    var pr = FX.progress;
    var foot = pr && pr.target
      ? 'Every pair of teams plays <b>' + pr.target + '</b> best-of-three' +
        (pr.target === 1 ? '' : 's') + ' over the season.'
      : '';
    if (FX.season && FX.season.final) {
      foot += ' The top two then meet in the <b>final</b>.';
    }
    foot += ' What has not been played yet is in the schedule below.';

    host.innerHTML =
      '<div class="sp card">' +
        '<div class="sp-head">' +
          '<div>' +
            '<div class="sp-title">Matches played</div>' +
            '<div class="sp-sub"><b>' + done + '</b> of ' + total +
              ' played &middot; <b>' + left + '</b> still to play' +
              (live ? ' &middot; ' + live + ' in progress' : '') +
            '</div>' +
          '</div>' +
          '<div class="sp-meter" role="img" aria-label="' + pct + '% of the season played">' +
            '<div class="sp-meter__fill" style="width:' + pct + '%"></div>' +
          '</div>' +
        '</div>' +
        (rows.length
          ? '<div class="sp-scroll"><table class="sp-tbl">' +
              '<thead><tr><th>Match</th><th>When</th><th>Result</th></tr></thead>' +
              '<tbody>' + body + '</tbody>' +
            '</table></div>'
          : '<div class="sp-none">No matches have been played yet. ' +
            'Results arrive by posting the post-game screenshot in ' +
            '<b>#dota-league-2026</b>.</div>') +
        (foot ? '<div class="sp-foot">' + foot + '</div>' : '') +
      '</div>';
  }

  function drawSchedule() {
    drawProgress();
    var host = $("#scheduleBody");
    if (!host) return;
    fxCopy();
    if (!FX || !FX.weeks || !FX.weeks.length) {
      host.innerHTML = '<div class="coord-section coord-section--empty">' +
        'No schedule has been generated yet.</div>';
      return;
    }
    var cur = currentNight();
    if (state.fxView === "list") drawList(host, cur);
    else drawTimeline(host, cur);
  }

  /* ── Series (best-of-three results) ───────────────────────────────
     The Schedule tab answers "when do we play". This one answers "what
     happened" — each best-of-three opened up into its individual games,
     with the same scoreboard the Matches tab draws, because a series
     score of 2–1 is meaningless without being able to see the three
     games behind it.

     A series appears here only once a result has been recorded against
     it by tools/league_result.py. Nothing on this tab is inferred from
     rosters: the Teams tab comment explains why that guessing put a team
     on 1-0 before a league game had been played. */

  // source_ref -> LEAGUE match. Indexed from the league ledger only: a
  // series game can never resolve to a lobby match, because the two
  // ledgers share no rows at all.
  var BY_REF = {};
  TOUR.forEach(function (m) {
    if (m.source_ref) BY_REF[m.source_ref] = m;
  });

  /* Player records WITHIN the league. Computed from TOUR alone, so a
     player's inhouse form has no effect on their tournament line and
     vice versa — that separation is the entire point of the second
     ledger. Wilson rating is the same function the lobby board uses. */
  function aggregateLeaguePlayers() {
    var P = {};
    TOUR.forEach(function (m) {
      [["radiant", m.radiant_team_id], ["dire", m.dire_team_id]]
        .forEach(function (pair) {
          (m[pair[0]] || []).forEach(function (r) {
            var p = P[r.name] || (P[r.name] = {
              name: r.name, team: pair[1], games: 0, wins: 0, losses: 0,
              k: 0, d: 0, a: 0, gpm: 0, heroes: {}
            });
            p.games++;
            if (r.won) p.wins++; else p.losses++;
            p.k += r.k || 0; p.d += r.d || 0; p.a += r.a || 0;
            p.gpm += r.gpm || 0;
            if (r.hero) p.heroes[r.hero] = (p.heroes[r.hero] || 0) + 1;
          });
        });
    });
    var list = [];
    for (var n in P) {
      var p = P[n];
      p.winPct = p.games ? (p.wins / p.games) * 100 : 0;
      p.kda = p.d ? (p.k + p.a) / p.d : (p.k + p.a);
      // Rounded here, not at render: num() formats but does not round, so
      // an unrounded average prints as "560.667" beside the lobby board's
      // clean integers.
      p.avgGpm = p.games ? Math.round(p.gpm / p.games) : 0;
      p.rating = wilson(p.wins, p.games, state.evidence) * SCALE;
      // Most-played hero, for the portrait and so search matches on it —
      // same as the lobby board.
      p.topHero = Object.keys(p.heroes).sort(function (x, y) {
        return p.heroes[y] - p.heroes[x];
      })[0] || null;
      list.push(p);
    }
    return list.sort(function (a, b) {
      return b.rating - a.rating || b.games - a.games || a.name.localeCompare(b.name);
    });
  }

  function allSeries() {
    var out = [];
    if (!FX) return out;
    FX.weeks.forEach(function (w) {
      w.nights.forEach(function (n) {
        n.series.forEach(function (s) { out.push({ w: w, n: n, s: s }); });
      });
    });
    return out;
  }

  /* One recorded game, drawn with the same board() the Matches tab uses.
     Reusing it is deliberate — a league game and an inhouse game are the
     same kind of thing and should not read as two different objects. */
  function renderSeriesGame(g, s, idx) {
    var m = BY_REF[g.source_ref];
    var winName = fxTeam(g.winner);

    if (!m) {
      // Recorded, but the match is missing from the ledger. Say so loudly
      // rather than render a blank card — a game that counts towards a
      // team's record but cannot be inspected is exactly the quiet wrong
      // number this project exists to prevent.
      return '<div class="sg sg--orphan">' +
        '<div class="sg__head">' +
          '<span class="sg__n">Game ' + g.game_no + '</span>' +
          '<span class="sg__missing">Result recorded, but no match found for ' +
            '<code>' + esc(g.source_ref) + '</code></span>' +
        '</div></div>';
    }

    var rWon = m.winning_side === "radiant";
    return '<div class="sg" data-ref="' + esc(g.source_ref) + '">' +
      '<button class="sg__head" aria-expanded="false">' +
        '<span class="sg__n">Game ' + g.game_no + '</span>' +
        '<span class="sg__side sg__side--radiant' + (rWon ? " is-won" : "") + '">' +
          esc(m.radiant_team_name || "The Radiant") +
          '<span class="sg__tag">Radiant</span></span>' +
        '<span class="sg__score">' +
          '<span class="' + (rWon ? "win" : "") + '">' + m.radiant_score + '</span>' +
          '<span class="sep">–</span>' +
          '<span class="' + (rWon ? "" : "win") + '">' + m.dire_score + '</span></span>' +
        '<span class="sg__side sg__side--dire' + (rWon ? "" : " is-won") + '">' +
          esc(m.dire_team_name || "The Dire") +
          '<span class="sg__tag">Dire</span></span>' +
        '<span class="sg__won">' +
          '<span class="team-chip team-chip--' + g.winner + '">' + g.winner + '</span>' +
          esc(winName) + '</span>' +
        '<span class="sg__meta">' + esc(dur(m.duration_seconds)) +
          '<span class="chev" aria-hidden="true"></span></span>' +
      '</button>' +
      '<div class="sg__body"><div class="sg__inner"></div></div>' +
    '</div>';
  }

  function seriesStatus(s) {
    var n = (s.games || []).length;
    if (!n) return { cls: "is-todo", label: "Not played" };
    if (s.status === "final") return { cls: "is-final", label: "Final" };
    return { cls: "is-live", label: "Game " + n + " of " + (s.best_of || 3) };
  }

  function renderSeriesCard(e) {
    var s = e.s, a = s.teams[0], b = s.teams[1];
    var sc = s.score || [0, 0];
    var games = s.games || [];
    var st = seriesStatus(s);
    var open = games.length > 0;

    function side(i) {
      var tid = s.teams[i];
      var won = games.length && s.status === "final" && sc[i] > sc[1 - i];
      return '<span class="sr-side' + (won ? " is-won" : "") + '">' +
        '<span class="team-chip team-chip--' + tid + '">' + tid + '</span>' +
        '<span class="sr-team">' + esc(fxTeam(tid)) + '</span></span>';
    }

    return '<div class="sr' + (open ? " is-open" : "") + '" data-sid="' + esc(s.id) + '">' +
      '<button class="sr__head" aria-expanded="' + (open ? "true" : "false") + '">' +
        '<span class="sr__when">' +
          '<span class="sr__date">' + esc(e.n.day) + " " + esc(dayLabel(e.n.date)) + '</span>' +
        '</span>' +
        '<span class="sr__slot' + (s.slot === 2 ? " is-late" : "") + '">' +
          (s.slot === 2 ? "Late" : "Early") + '</span>' +
        side(0) +
        '<span class="sr__score">' + (games.length ? sc[0] + " – " + sc[1] : "–") + '</span>' +
        side(1) +
        '<span class="sr__status ' + st.cls + '">' + esc(st.label) + '</span>' +
        '<span class="chev" aria-hidden="true"></span>' +
      '</button>' +
      '<div class="sr__body"><div class="sr__inner">' +
        (games.length
          ? games.map(function (g, i) { return renderSeriesGame(g, s, i); }).join("")
          : '<p class="sr__none">No games recorded yet. Results arrive by posting ' +
            'the post-game screenshot in <b>#dota-league-2026</b>.</p>') +
      '</div></div>' +
    '</div>';
  }

  /* Boards are built on first expand rather than up front: a full season
     of three-game series is a lot of DOM to create for rows nobody opens. */
  function wireSeriesGame(row) {
    var head = row.querySelector(".sg__head");
    if (!head) return;
    head.addEventListener("click", function () {
      var open = row.classList.toggle("is-open");
      head.setAttribute("aria-expanded", open ? "true" : "false");
      var inner = row.querySelector(".sg__inner");
      if (!open || inner.childNodes.length) return;
      var m = BY_REF[row.getAttribute("data-ref")];
      if (!m) return;
      var rWon = m.winning_side === "radiant";
      var w1 = el("div", "board-wrap");
      w1.appendChild(board("radiant", m.radiant, m.radiant_team_name || "The Radiant", rWon));
      var w2 = el("div", "board-wrap");
      w2.appendChild(board("dire", m.dire, m.dire_team_name || "The Dire", !rWon));
      inner.appendChild(w1); inner.appendChild(w2);
      if (m.notes) inner.appendChild(el("p", "match__note", esc(m.notes)));
    });
  }

  /* Shared sort used by both tournament tables. Same tie-breaking as the
     lobby board: evidence first, then performance, so sorting by any
     column can never float a 1-4 record above a 3-2 one. */
  function tourSort(list, key, dir) {
    return list.slice().sort(function (a, b) {
      var x = a[key], y = b[key];
      if (key === "name") return dir * String(x).localeCompare(String(y));
      if (x === null || x === undefined) x = dir === -1 ?  Infinity : -Infinity;
      if (y === null || y === undefined) y = dir === -1 ?  Infinity : -Infinity;
      if (x !== y) return dir * (x - y);
      if (a.games !== b.games)   return b.games - a.games;
      if (a.winPct !== b.winPct) return b.winPct - a.winPct;
      return String(a.name).localeCompare(String(b.name));
    });
  }

  /* Team standings need their own tie-break. The generic one above puts
     MORE GAMES first, which is right on the individual board — more games
     is more evidence — and wrong here. Team 2 went 4–1 and Team 3 went
     4–2, both reaching 10 points, and "more games" ranked the worse
     record first. A points table breaks a tie the way every league does:
     series won, then game difference, then win rate. */
  function teamSort(list, key, dir) {
    return list.slice().sort(function (a, b) {
      var x = a[key], y = b[key];
      if (key === "name") return dir * String(x).localeCompare(String(y));
      if (x === null || x === undefined) x = dir === -1 ?  Infinity : -Infinity;
      if (y === null || y === undefined) y = dir === -1 ?  Infinity : -Infinity;
      if (x !== y) return dir * (x - y);
      if (a.seriesWins !== b.seriesWins) return b.seriesWins - a.seriesWins;
      var ad = a.wins - a.losses, bd = b.wins - b.losses;
      if (ad !== bd)             return bd - ad;
      if (a.winPct !== b.winPct) return b.winPct - a.winPct;
      return String(a.name).localeCompare(String(b.name));
    });
  }

  function sortHead(cols, key, dir, attr) {
    return '<tr>' + cols.map(function (c) {
      if (!c.k) return '<th class="' + (c.cls || "") + '">' + c.t + '</th>';
      var on = c.k === key;
      return '<th class="' + (c.cls || "") + '" ' + attr + '="' + c.k + '"' +
        (on ? ' aria-sort="' + (dir === 1 ? "ascending" : "descending") + '"' : '') +
        (c.title ? ' title="' + esc(c.title) + '"' : '') + '>' + c.t + '</th>';
    }).join("") + '</tr>';
  }

  /* Which opponents a team still owes a series to. Chips rather than
     text: at five teams the names would wrap and the column is scanned,
     not read. A count above 1 is stacked on the chip. */
  function leftCell(t) {
    if (!t.vsLeft || !t.vsLeft.length) {
      return '<span class="tt-none">' +
        (t.remaining === 0 ? "season complete" : "—") + '</span>';
    }
    return '<span class="tt-left-list">' + t.vsLeft.map(function (v) {
      // A series already under way is not the same as one not started —
      // it is the difference between "finish this" and "arrange that".
      var live = v.playing > 0;
      return '<span class="tt-opp' + (live ? " is-live" : "") + '"' +
             (live ? ' title="series in progress"' : '') + '>' +
        '<span class="team-chip team-chip--' + v.id + '">' + v.id + '</span>' +
        (v.remaining > 1 ? '<span class="tt-opp__n">×' + v.remaining + '</span>' : '') +
      '</span>';
    }).join("") + '</span>';
  }

  /* ── Tournament: team standings, ranked by POINTS ── */
  var TEAM_COLS = [
    { t: "#", cls: "c-rank" },
    { t: "Team", cls: "c-player", k: "name" },
    { t: "Pts", cls: "c-num c-rating", k: "points",
      title: "3 for winning a best-of-three, 1 for every game won" },
    { t: "Series", cls: "c-num", k: "seriesPlayed" },
    { t: "Left", cls: "c-num", k: "remaining",
      title: "Best-of-threes this team still has to play" },
    { t: "Still to play", cls: "c-player" },
    { t: "SW", cls: "c-num", k: "seriesWins", title: "Best-of-threes won" },
    { t: "SL", cls: "c-num", k: "seriesLosses", title: "Best-of-threes lost" },
    { t: "GP", cls: "c-num", k: "games", title: "Individual games played" },
    { t: "W", cls: "c-num", k: "wins" },
    { t: "L", cls: "c-num", k: "losses" },
    { t: "Win rate", cls: "c-bar", k: "winPct" },
    { t: "%", cls: "c-num", k: "winPct" },
    { t: "Rating", cls: "c-num", k: "rating",
      title: "Game win rate adjusted for how many games back it up" }
  ];

  function renderTeamTable() {
    var agg = aggregateTeams();
    if (!agg) return "";
    var any = agg.teams.some(function (t) { return t.games > 0; });
    // The schedule knows what is still owed; the ledger knows what has been
    // won. They are separate files on purpose, and this is the one place
    // the two are put side by side.
    var prog = (FX && FX.progress) ? FX.progress.teams : null;
    agg.teams.forEach(function (t) {
      var pt = prog && prog.filter(function (x) { return x.id === t.id; })[0];
      t.remaining = pt ? pt.remaining : null;
      t.vsLeft = pt ? pt.vs.filter(function (v) { return v.remaining > 0; }) : [];
    });
    var teams = teamSort(agg.teams, state.trSort, state.trDir);
    var rows = teams.map(function (t, i) {
      var lead = i === 0 && t.games && state.trSort === "points";
      return '<tr' + (lead ? ' class="is-lead"' : '') + ' style="--i:' + i + '">' +
        '<td><span class="rank">' + (t.games ? i + 1 : "–") + '</span></td>' +
        '<td class="c-player"><span class="tt-team">' +
          '<span class="team-chip team-chip--' + t.id + '">' + t.id + '</span>' +
          esc(t.name) + '</span></td>' +
        '<td class="c-rating"><span class="pts' + (t.points ? "" : " is-zero") + '">' +
          t.points + '</span></td>' +
        '<td class="dim">' + (t.seriesPlayed || "—") + '</td>' +
        '<td class="c-num"><span class="tt-left' + (t.remaining ? "" : " is-done") + '">' +
          (t.remaining === null ? "—" : t.remaining) + '</span></td>' +
        '<td class="c-player">' + leftCell(t) + '</td>' +
        '<td class="w-num">' + t.seriesWins + '</td>' +
        '<td class="l-num">' + t.seriesLosses + '</td>' +
        '<td>' + t.games + '</td>' +
        '<td class="w-num">' + t.wins + '</td>' +
        '<td class="l-num">' + t.losses + '</td>' +
        '<td><div class="meter"><span style="width:' + t.winPct.toFixed(1) + '%"></span></div></td>' +
        '<td class="pct">' + (t.games ? pct(t.winPct) : "—") + '</td>' +
        '<td class="c-rating"><span class="rating' + ratingTier(t.rating) + '">' +
          (t.games ? t.rating.toFixed(1) : "—") + '</span></td>' +
      '</tr>';
    }).join("");
    return '<div class="card table-card tt-block">' +
      '<div class="tt-block__head">Team standings' +
        '<span class="tt-block__sub">' + (any
          ? '<b>3 points</b> for taking a best-of-three, <b>1 point</b> for every ' +
            'game won. <b>Left</b> is how many best-of-threes that team still owes, ' +
            'and <b>Still to play</b> names them — a ringed badge is a series already ' +
            'under way. League ledger only — inhouse games are not counted.'
          : 'No league games recorded yet.') + '</span></div>' +
      '<div class="table-scroll"><table class="grid" id="ttTeams">' +
        '<thead>' + sortHead(TEAM_COLS, state.trSort, state.trDir, "data-tsort") +
        '</thead><tbody>' + rows + '</tbody>' +
      '</table></div></div>';
  }

  /* ── Tournament: player leaderboard (league only) ── */
  var PLAYER_COLS = [
    { t: "#", cls: "c-rank" },
    { t: "Player", cls: "c-player", k: "name" },
    { t: "GP", cls: "c-num", k: "games" },
    { t: "W", cls: "c-num", k: "wins" },
    { t: "L", cls: "c-num", k: "losses" },
    { t: "Win rate", cls: "c-bar", k: "winPct" },
    { t: "%", cls: "c-num", k: "winPct" },
    { t: "K / D / A", cls: "c-kda" },
    { t: "KDA", cls: "c-num", k: "kda" },
    { t: "GPM", cls: "c-num c-opt", k: "avgGpm" },
    { t: "Rating", cls: "c-num c-rating", k: "rating",
      title: "Win rate adjusted for how many games back it up" }
  ];

  function renderLeaguePlayers() {
    var all = aggregateLeaguePlayers();
    var list = tourSort(all.filter(function (p) {
      if (p.games < state.tourMin) return false;
      return match(state.qTour, [p.name, p.topHero]);
    }), state.trPSort, state.trPDir);

    var rows = list.map(function (p, i) {
      return '<tr style="--i:' + i + '">' +
        '<td><span class="rank">' + (i + 1) + '</span></td>' +
        '<td class="c-player"><span class="who">' +
          '<span class="team-chip team-chip--' + p.team + '">' + p.team + '</span>' +
          faceTag(p.topHero, "who__face") +
          '<span><span class="who__name">' + esc(p.name) + '</span>' +
          (p.topHero ? '<span class="who__hero">' + esc(p.topHero) + '</span>' : "") +
          '</span></span></td>' +
        '<td>' + p.games + '</td>' +
        '<td class="w-num">' + p.wins + '</td>' +
        '<td class="l-num">' + p.losses + '</td>' +
        '<td><div class="meter"><span style="width:' + p.winPct.toFixed(1) + '%"></span></div></td>' +
        '<td class="pct">' + pct(p.winPct) + '</td>' +
        '<td class="c-kda">' + p.k + " / " + p.d + " / " + p.a + '</td>' +
        '<td>' + rat(p.kda) + '</td>' +
        '<td class="c-opt dim">' + num(p.avgGpm) + '</td>' +
        '<td class="c-rating"><span class="rating' + ratingTier(p.rating) + '">' +
          p.rating.toFixed(1) + '</span></td>' +
      '</tr>';
    }).join("") || '<tr><td colspan="11" class="none">No players match that filter.</td></tr>';

    return '<div class="card table-card tt-block">' +
      '<div class="tt-block__head">Players in the league' +
        '<span class="tt-block__sub">Tournament games only — these numbers are ' +
        'completely independent of the <b>Standings</b> tab.</span></div>' +
      '<div class="table-scroll"><table class="grid" id="ttPlayers">' +
        '<thead>' + sortHead(PLAYER_COLS, state.trPSort, state.trPDir, "data-psort") +
        '</thead><tbody>' + rows + '</tbody>' +
      '</table></div></div>';
  }

  /* Header clicks re-sort. Wired after every draw because the tables are
     rebuilt as innerHTML rather than patched. */
  function wireTourSort(host) {
    Array.prototype.forEach.call(host.querySelectorAll("[data-tsort]"), function (th) {
      th.addEventListener("click", function () {
        var k = th.getAttribute("data-tsort");
        if (k === state.trSort) state.trDir = -state.trDir;
        else { state.trSort = k; state.trDir = (k === "name") ? 1 : -1; }
        drawSeries();
      });
    });
    Array.prototype.forEach.call(host.querySelectorAll("[data-psort]"), function (th) {
      th.addEventListener("click", function () {
        var k = th.getAttribute("data-psort");
        if (k === state.trPSort) state.trPDir = -state.trPDir;
        else { state.trPSort = k; state.trPDir = (k === "name") ? 1 : -1; }
        drawSeries();
      });
    });
  }

  /* ── Tournament: every league game, attached to a series or not ──
     Showing unattached games matters: a screenshot posted before the
     schedule is settled would otherwise vanish, which is exactly the
     "where did my screenshot go" failure. */
  function renderLeagueGames() {
    if (!TOUR.length) return "";
    var linked = {};
    allSeries().forEach(function (e) {
      (e.s.games || []).forEach(function (g) { linked[g.source_ref] = e.s.id; });
    });
    var cards = TOUR.slice().reverse().map(function (m) {
      var rWon = m.winning_side === "radiant";
      var tag = linked[m.source_ref]
        ? '<span class="tg__series">' + esc(linked[m.source_ref]) + '</span>'
        : '<span class="tg__series is-loose" title="Recorded, but not yet part of a ' +
          'best-of-three">Unattached</span>';
      return '<div class="sg tg" data-ref="' + esc(m.source_ref) + '">' +
        '<button class="sg__head" aria-expanded="false">' +
          '<span class="sg__n">' + esc(m.played_on || "undated") + '</span>' +
          '<span class="sg__side sg__side--radiant' + (rWon ? " is-won" : "") + '">' +
            esc(m.radiant_team_name || "The Radiant") +
            '<span class="sg__tag">Team ' + m.radiant_team_id + '</span></span>' +
          '<span class="sg__score">' +
            '<span class="' + (rWon ? "win" : "") + '">' + m.radiant_score + '</span>' +
            '<span class="sep">–</span>' +
            '<span class="' + (rWon ? "" : "win") + '">' + m.dire_score + '</span></span>' +
          '<span class="sg__side sg__side--dire' + (rWon ? "" : " is-won") + '">' +
            esc(m.dire_team_name || "The Dire") +
            '<span class="sg__tag">Team ' + m.dire_team_id + '</span></span>' +
          '<span class="sg__won">' +
            '<span class="team-chip team-chip--' + m.winner_team_id + '">' +
              m.winner_team_id + '</span>won</span>' +
          '<span class="sg__meta">' + tag +
            '<span class="chev" aria-hidden="true"></span></span>' +
        '</button>' +
        '<div class="sg__body"><div class="sg__inner"></div></div>' +
      '</div>';
    }).join("");
    return '<div class="card tt-block tt-block--games">' +
      '<div class="tt-block__head">League games' +
        '<span class="tt-block__sub">Every match in the league ledger. Open one ' +
        'for the full scoreboard.</span></div>' + cards + '</div>';
  }

  function drawSeries() {
    var host = $("#seriesBody");
    if (!host) return;

    var all = FX && FX.weeks && FX.weeks.length ? allSeries() : [];
    var played = all.filter(function (e) { return (e.s.games || []).length > 0; });
    var today = new Date().toISOString().slice(0, 10);
    var next = all.filter(function (e) {
      return !(e.s.games || []).length && e.n.date >= today;
    });

    var counts = $("#seriesCount");
    if (counts) {
      counts.textContent = TOUR.length
        ? TOUR.length + (TOUR.length === 1 ? " game · " : " games · ") +
          played.length + " series"
        : "Nothing recorded yet";
    }

    var bar = $("#tourFilters");
    if (bar) bar.hidden = state.srView !== "standings";

    if (state.srView === "standings") {
      host.innerHTML = renderTeamTable() + renderLeaguePlayers() +
        (TOUR.length ? "" : emptyTournament(next.length));
      wireTourSort(host);
      return;
    }

    if (state.srView === "games") {
      host.innerHTML = TOUR.length ? renderLeagueGames() : emptyTournament(next.length);
      Array.prototype.forEach.call(host.querySelectorAll(".sg"), wireSeriesGame);
      return;
    }

    // Series view.
    if (!all.length) {
      host.innerHTML = '<div class="coord-section coord-section--empty">' +
        'No schedule has been generated yet.</div>';
      return;
    }
    var list = played.slice().reverse().concat(next.slice(0, 8));
    if (!list.length) { host.innerHTML = emptyTournament(next.length); return; }

    host.innerHTML = list.map(renderSeriesCard).join("");
    Array.prototype.forEach.call(host.querySelectorAll(".sr"), function (card) {
      var head = card.querySelector(".sr__head");
      head.addEventListener("click", function () {
        var open = card.classList.toggle("is-open");
        head.setAttribute("aria-expanded", open ? "true" : "false");
      });
    });
    Array.prototype.forEach.call(host.querySelectorAll(".sg"), wireSeriesGame);
  }

  function emptyTournament(upcoming) {
    return '<div class="sr-empty card">' +
      '<h3>Nothing recorded yet</h3>' +
      '<p>The league keeps its own ledger, separate from the inhouse lobby. ' +
        'A game shows up here — and only here — once its screenshot has been ' +
        'read and checked.</p>' +
      '<ol>' +
        '<li>Post the post-game screenshot in <b>#dota-league-2026</b>.</li>' +
        '<li>It is checked against the kills/score/deaths chain, and refused ' +
          'unless each side is exactly one team\'s roster.</li>' +
        '<li>It lands in the league ledger, and is attached to a scheduled ' +
          'best-of-three.</li>' +
      '</ol>' +
      '<p class="sr-empty__note">' + upcoming + ' scheduled series waiting. ' +
        'Nothing here affects the Standings tab, and nothing there affects ' +
        'this page.</p>' +
    '</div>';
  }

  var COORD = D.coord || null;

  var TZ_LABELS = [
    ["Karachi",  "Asia/Karachi"],
    ["New York", "America/New_York"],
    ["Riyadh",   "Asia/Riyadh"],
    ["Berlin",   "Europe/Berlin"]
  ];

  function fmtLocal(iso) {
    // iso is already in the local zone from Python; just re-render the
    // human-readable part. Falls back to the raw ISO if Date parsing fails.
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
    // Extract the hour/minute portion from ISO (avoid Date.getHours which
    // would apply the BROWSER's tz, undoing the Python conversion).
    var m = iso.match(/T(\d{2}):(\d{2})/);
    var hhmm = m ? (m[1] + ":" + m[2]) : "??:??";
    return days[d.getUTCDay()] + " " + hhmm;
  }

  function fmtUtc(iso) {
    var m = iso.match(/(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    if (!m) return iso;
    var days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
    var date = new Date(iso);
    return days[date.getUTCDay()] + " " + Number(m[3]) + " " + MON[Number(m[2]) - 1]
           + " · " + m[4] + ":" + m[5] + " UTC";
  }

  function renderNextMatch(m) {
    // Confirmed upcoming match. Shape from scheduling.json's `upcoming`.
    var tzRows = TZ_LABELS.map(function (pair) {
      // We need per-tz rendering. If the confirmed match doesn't carry
      // it, fall back to UTC only. (find_slot output includes renderings
      // for confirmed matches; if a raw upcoming entry is written by
      // hand it may not.)
      var r = (m.renderings || []).filter(function (x) {
        return x.iana === pair[1] || x.zone_label === pair[0];
      })[0];
      var when = r ? fmtLocal(r.local) : "—";
      return '<div class="tz-row"><span class="tz-row__label">' + esc(pair[0]) +
             '</span><span class="tz-row__time">' + esc(when) + '</span></div>';
    }).join("");

    return '<div class="next-match card">' +
      '<div class="next-match__head">' +
        '<span class="next-match__tag">Next Match</span>' +
        '<span class="next-match__utc">' + esc(fmtUtc(m.start_utc)) + '</span>' +
      '</div>' +
      '<div class="next-match__pair">' +
        '<span class="team-chip team-chip--' + m.match_up[0] + '">' + m.match_up[0] + '</span>' +
        '<span class="next-match__name">' + esc(m.match_up_names[0]) + '</span>' +
        '<span class="next-match__vs">vs</span>' +
        '<span class="next-match__name">' + esc(m.match_up_names[1]) + '</span>' +
        '<span class="team-chip team-chip--' + m.match_up[1] + '">' + m.match_up[1] + '</span>' +
      '</div>' +
      '<div class="next-match__tzs">' + tzRows + '</div>' +
    '</div>';
  }

  function renderSlot(slot, idx) {
    var tzRows = slot.renderings.map(function (r) {
      return '<div class="tz-row"><span class="tz-row__label">' + esc(r.zone_label) +
             '</span><span class="tz-row__time">' + esc(fmtLocal(r.local)) + '</span></div>';
    }).join("");

    var teamRows = Object.keys(slot.per_team).sort().map(function (tid) {
      var n = slot.per_team[tid];
      var missing = (slot.missing[tid] || []).slice(0, 3).join(", ");
      var more = (slot.missing[tid] || []).length > 3
        ? ' <span class="dim">(+' + (slot.missing[tid].length - 3) + ' more)</span>' : "";
      return '<div class="slot__team-row">' +
        '<span class="team-chip team-chip--' + tid + '">' + tid + '</span>' +
        '<span class="slot__team-avail">' + n + '/' + (n + (slot.missing[tid] || []).length) + '</span>' +
        (missing ? '<span class="slot__team-missing">missing: ' + esc(missing) + more + '</span>' : '') +
      '</div>';
    }).join("");

    var playable = Object.keys(slot.per_team).every(function (k) { return slot.per_team[k] >= 3; });

    return '<div class="slot' + (idx === 0 && playable ? ' slot--best' : '') + '">' +
      '<div class="slot__head">' +
        '<span class="slot__idx">' + (idx + 1) + '</span>' +
        '<span class="slot__utc">' + esc(fmtUtc(slot.start_utc)) + '</span>' +
        '<span class="slot__dur">' + slot.duration_min + " min window</span>" +
        '<span class="slot__total">' + slot.total + " available</span>" +
      '</div>' +
      '<div class="slot__body">' +
        '<div class="slot__tzs">' + tzRows + '</div>' +
        '<div class="slot__teams">' + teamRows + '</div>' +
      '</div>' +
    '</div>';
  }

  function renderRound(r) {
    var slotsHtml = r.slots && r.slots.length
      ? r.slots.map(renderSlot).join("")
      : '<div class="slot slot--empty">No overlapping windows yet — waiting for more availability.</div>';

    var warnsHtml = (r.warnings || []).length
      ? '<ul class="round__warnings">' +
          r.warnings.map(function (w) { return '<li>' + esc(w) + '</li>'; }).join("") +
        '</ul>'
      : "";

    return '<div class="round card">' +
      '<div class="round__head">' +
        '<span class="round__id">Round ' + esc(r.round_id) + '</span>' +
        '<span class="round__matchup">' +
          '<span class="team-chip team-chip--' + r.match_up[0] + '">' + r.match_up[0] + '</span>' +
          esc(r.match_up_names[0]) + ' vs ' + esc(r.match_up_names[1]) +
          '<span class="team-chip team-chip--' + r.match_up[1] + '">' + r.match_up[1] + '</span>' +
        '</span>' +
        '<span class="round__meta">' +
          r.respondents.length + '/' + r.roster_size + ' responded' +
          (r.week_of ? ' · week of ' + esc(r.week_of) : '') +
        '</span>' +
      '</div>' +
      (r.note ? '<div class="round__note">' + esc(r.note) + '</div>' : '') +
      '<div class="round__slots">' + slotsHtml + '</div>' +
      warnsHtml +
    '</div>';
  }

  function drawCoord() {
    var wrap = $("#coordBody");
    if (!wrap) return;
    if (!COORD) {
      wrap.innerHTML = '<p class="none">Scheduling data not loaded. Add data/scheduling.json and re-export.</p>';
      return;
    }

    var chunks = [];

    // Next-match card(s)
    if (COORD.upcoming && COORD.upcoming.length) {
      chunks.push('<div class="coord-section">' +
        COORD.upcoming.map(renderNextMatch).join("") +
      '</div>');
    } else {
      chunks.push('<div class="coord-section coord-section--empty">' +
        '<div class="empty-card card">' +
          '<span class="empty-card__tag">Next Match</span>' +
          '<p>No confirmed match yet. Once a captain runs <code>!confirm N</code> in Discord, the winner appears here.</p>' +
        '</div>' +
      '</div>');
    }

    // Open rounds
    if (COORD.open_rounds && COORD.open_rounds.length) {
      chunks.push('<div class="coord-section">' +
        '<h3 class="coord-section__title">Open scheduling rounds</h3>' +
        COORD.open_rounds.map(renderRound).join("") +
      '</div>');
    } else {
      chunks.push('<div class="coord-section coord-section--empty">' +
        '<div class="empty-card card">' +
          '<span class="empty-card__tag">Open rounds</span>' +
          '<p>No scheduling round is open. A captain can start one with <code>!schedule Team X vs Team Y</code>.</p>' +
        '</div>' +
      '</div>');
    }

    wrap.innerHTML = chunks.join("");
  }

  /* ── Mini Cup — the short format ──────────────────────────────────
     A PROPOSAL, not a season. Two pools of three play a round robin,
     the top two of each go into a four-team double-elimination bracket,
     and the third in each pool is out.

     Every word and number below comes out of LOBBY.mini, which
     export_web.build_mini() derives from data/mini_tournament.json.
     Nothing here is typed: change a pool in that file and the matches,
     the boxes, the counts and the copy all move together. The Schedule
     tab's prose went false the first time the season was regenerated —
     prose has no checksum, so it does not get to hold facts.

     Nothing on this tab is a result either. If the league ever plays
     this, the games go through tools/league_ingest.py into the league
     ledger like any other league game. */

  var MINI = D.mini || null;

  var MI_TEAM = {};
  if (MINI) {
    MINI.pools.forEach(function (p) {
      p.teams.forEach(function (t) { MI_TEAM[t.id] = t; });
    });
  }

  function miTeam(id) {
    return MI_TEAM[id] ||
      { id: id, name: "Team " + id, provisional: true, players: [], unfilled: 0 };
  }

  /* A team that is not on a roster in data/teams.json is drawn with a
     dashed ring, everywhere it appears. Team 6 is two named people and
     three empty chairs; it must never read as a settled side. */
  function miChip(id) {
    var t = miTeam(id);
    return '<span class="team-chip team-chip--' + id +
      (t.provisional ? " is-prov" : "") + '">' + id + '</span>';
  }

  function miPill(id) {
    var t = miTeam(id);
    return '<span class="team-pill team-pill--' + id +
      (t.provisional ? " is-prov" : "") + '">' + esc(t.name) + '</span>';
  }

  function miBo(n) { return n === 1 ? "one game" : "best of " + n; }

  // "3th" shipped once. 11th-13th are the exception every naive version
  // of this gets wrong, so they are handled even though a pool of eleven
  // is not on the cards.
  function miOrd(n) {
    var t = n % 100;
    if (t >= 11 && t <= 13) return n + "th";
    return n + (["th", "st", "nd", "rd"][n % 10] || "th");
  }

  /* Where each place in a pool goes next, read out of the bracket's own
     feeds rather than typed here. Move a pool around in the config and
     this follows; type it and it is one edit away from being a lie. */
  function miExits(p) {
    var out = [];
    MINI.bracket.forEach(function (n) {
      n.feeds.forEach(function (f) {
        if (f.kind === "pool" && f.pool === p.id) {
          out.push({ rank: f.rank, to: n.round.toLowerCase() });
        }
      });
    });
    out.sort(function (a, b) { return a.rank - b.rank; });
    for (var r = out.length + 1; r <= p.teams.length; r++) {
      out.push({ rank: r, to: "out", gone: true });
    }
    return out;
  }

  /* One row of a pool's table. Drawn from `standings`, which the export
     orders by rank where the results settle one and by wins otherwise --
     so the card is a live table from the first game, and a finished pool
     reads top to bottom in finishing order. A pool that the results do
     NOT separate says "tie-break" rather than picking somebody. */
  function miPoolRow(p, t, anyPlayed) {
    var team = miTeam(t.id);
    var meta = "";
    if (team.provisional) {
      meta = team.players.length
        ? team.players.join(", ") + (team.unfilled
            ? " · " + team.unfilled + " still to name" : "")
        : "not confirmed";
    }
    var tag = t.outcome === "advances" ? '<span class="mc-tag is-adv">Through</span>'
            : t.outcome === "out"      ? '<span class="mc-tag is-out">Out</span>'
            : t.outcome === "tied"     ? '<span class="mc-tag is-tied">Tie-break</span>'
            : "";
    return '<div class="mc-team' + (team.provisional ? " is-prov" : "") +
             (t.outcome === "out" ? " is-gone" : "") + '">' +
      '<span class="mc-team__pos">' + (t.rank || "") + '</span>' +
      miChip(t.id) +
      '<span class="mc-team__name">' + esc(team.name) + '</span>' +
      (meta ? '<span class="mc-team__meta">' + esc(meta) + '</span>' : "") +
      (anyPlayed ? '<span class="mc-team__rec">' + t.won + '–' + t.lost +
                   '</span>' : "") +
      tag +
    '</div>';
  }

  function miPool(p) {
    var anyPlayed = p.matches.some(function (m) { return m.winner != null; });
    var teams = p.standings.map(function (t) {
      return miPoolRow(p, t, anyPlayed);
    }).join("");

    return '<div class="mc-pool card">' +
      '<div class="mc-pool__head">' +
        '<span class="mc-pool__badge">' + esc(p.id) + '</span>' +
        '<span class="mc-pool__title">' + esc(p.label) + '</span>' +
        '<span class="mc-pool__adv">' + (
          p.complete && !p.decided ? 'Level — needs a tie-break'
          : p.complete ? 'Decided'
          : 'Top ' + p.advance + ' go through') + '</span>' +
      '</div>' +
      '<div class="mc-pool__teams">' + teams + '</div>' +
      '<div class="mc-pool__foot">' + miExits(p).map(function (x) {
        return '<span' + (x.gone ? ' class="is-out"' : '') + '><b>' +
          miOrd(x.rank) + '</b> ' + esc(x.to) + '</span>';
      }).join("") + '</div>' +
    '</div>';
  }

  /* The group stage as a table, built from the SAME component the
     Schedule tab uses for played matches (.sp / .sp-tbl). Sharing it
     means the two read alike and the phone treatment is already solved:
     below 620px each row becomes a block rather than scrolling the
     Result column off the edge, which is the column the table is for.

     It lists every pool's matches in one place rather than three inside
     each pool card -- six matches stated twice is two things to keep in
     step, and the pool card's job is the roster and where each place
     goes next. */
  function miGroupTable() {
    var ms = [];
    MINI.pools.forEach(function (p) {
      p.matches.forEach(function (m) { ms.push({ p: p, m: m }); });
    });

    var played = 0, reported = 0;
    var body = ms.map(function (x) {
      var w = x.m.winner;
      if (w != null) played++;
      if (x.m.reported) reported++;
      var res = w != null
        ? '<span class="sp-won">' + miChip(w) + esc(miTeam(w).name) +
            ' won</span>'
        : '<span class="sp-badge">Still to play</span>';
      return '<tr' + (w != null ? ' class="is-done"' : '') + '>' +
        '<td class="sp-td-m"><span class="sp-m">' +
          miPill(x.m.teams[0]) + '<span class="sp-vs">vs</span>' +
          miPill(x.m.teams[1]) + '</span></td>' +
        '<td class="sp-when"><span class="sp-pool">' + esc(x.p.id) + '</span>' +
          esc(x.p.label) + '</td>' +
        '<td class="sp-td-r"><span class="sp-res">' + res + '</span></td>' +
      '</tr>';
    }).join("");

    /* Where these results came from, said plainly.

       Everything else on this site is transcribed from a post-game
       screenshot and checksummed against
       `team kills <= team score <= enemy deaths` before it counts. A
       reported result is a name and nothing else — no scoreboard, no
       player lines, nothing to check it against. That is a real
       difference and the page is not going to hide it. */
    var foot;
    if (!played) {
      foot = 'Nothing has been played. This is the format drawn out, ' +
             'not a season that is running.';
    } else {
      foot = '<b>' + played + '</b> of ' + ms.length + ' played.';
      if (reported) {
        foot += ' <b>' + reported + '</b> of them ' +
          (reported === 1 ? 'was' : 'were') + ' reported rather than read ' +
          'from a screenshot, so there is no scoreboard behind ' +
          (reported === 1 ? 'it' : 'them') + ' and nothing to check ' +
          (reported === 1 ? 'it' : 'them') + ' against. Post the post-game ' +
          'shots in <b>#dota-league-2026</b> and they become real ledger ' +
          'games with every player\'s line behind them.';
      }
    }

    return '<div class="sp card mc-group">' +
      '<div class="sp-head">' +
        '<div>' +
          '<div class="sp-title">Group stage</div>' +
          '<div class="sp-sub"><b>' + ms.length + '</b> matches &middot; ' +
            miBo(MINI.best_of.pool) + ' each &middot; ' +
            'nobody meets the other pool until the bracket</div>' +
        '</div>' +
      '</div>' +
      '<div class="sp-scroll"><table class="sp-tbl">' +
        '<thead><tr><th>Match</th><th>Pool</th><th>Result</th></tr></thead>' +
        '<tbody>' + body + '</tbody>' +
      '</table></div>' +
      '<div class="sp-foot">' + foot + '</div>' +
    '</div>';
  }

  /* One bracket box: the round, the best-of, its two slots and what is
     at stake. A slot is drawn as a waiting placeholder because nobody
     has qualified — the label under it says where its occupant will
     come from, which is the only thing known today. */
  function miBox(n) {
    var slots = n.feeds.map(function (f, i) {
      var from = f.kind === "pool"
        ? f.label + " · Pool " + f.pool
        : f.label + " · " + miRound(f.node).toLowerCase();
      // Filled in only where a pool has actually decided it. Everything
      // else stays a placeholder -- see the export's feed resolution.
      var who = f.team != null
        ? miChip(f.team) + '<span class="br-team">' +
            esc(miTeam(f.team).name) + '</span>'
        : '<span class="br-ph" aria-hidden="true"></span>' +
          '<span class="br-from">' + esc(from) + '</span>';
      return '<div class="br-slot' + (f.team != null ? " is-set" : "") +
               '" data-slot="' + i + '">' + who +
        '<span class="br-sc">–</span>' +
      '</div>';
    }).join("");

    var row = n.row === "center" ? "1 / span 2" : String(n.row);
    return '<div class="br-box' + (n.final ? " is-final" : "") +
             (n.knockout && !n.final ? " is-knockout" : "") + '"' +
             ' data-node="' + n.id + '"' +
             ' style="grid-column:' + n.col + ';grid-row:' + row + '">' +
      '<div class="br-box__head">' +
        '<span class="br-round">' + esc(n.round) + '</span>' +
        '<span class="br-bo">' + miBo(n.best_of) + '</span>' +
      '</div>' +
      slots +
      '<div class="br-box__foot">' + esc(n.stakes) + '</div>' +
    '</div>';
  }

  function miRound(id) {
    var hit = MINI.bracket.filter(function (n) { return n.id === id; })[0];
    return hit ? hit.round : id;
  }

  /* The connector lines.

     Drawn as one SVG measured from the boxes AFTER layout rather than
     with CSS elbows, because the boxes are laid out by the grid at
     whatever width the browser gives them and a hard-coded elbow is
     right at exactly one width. The elbow sits just left of the target
     so two lines arriving at the same box share a gutter instead of
     crossing the box between them.

     A hidden tab measures 0×0 — every rect would be zero and the lines
     would collapse onto the origin. Hence the width guard, plus a
     ResizeObserver, which fires the moment the tab is shown and the
     grid goes from no size to its real one. */
  function miLines() {
    var grid = $("#brGrid"), svg = $("#brLines");
    if (!grid || !svg || !MINI) return;
    var g = grid.getBoundingClientRect();
    if (!g.width || !g.height) return;

    var out = [];
    var fanned = {};
    MINI.links.forEach(function (l) { fanned[l.from] = (fanned[l.from] || 0) + 1; });
    var seen = {};

    MINI.links.forEach(function (l) {
      var from = grid.querySelector('[data-node="' + l.from + '"]');
      var to = grid.querySelector('[data-node="' + l.to +
                                 '"] .br-slot[data-slot="' + l.slot + '"]');
      if (!from || !to) return;
      var a = from.getBoundingClientRect(), b = to.getBoundingClientRect();

      // A box that both a winner and a loser leave gets two anchors, one
      // above the other, so the two paths read as a fork rather than as
      // one line that mysteriously splits.
      var i = seen[l.from] = (seen[l.from] === undefined ? 0 : seen[l.from] + 1);
      var spread = fanned[l.from] > 1 ? (i === 0 ? -11 : 11) : 0;

      var x1 = a.right - g.left, y1 = a.top + a.height / 2 - g.top + spread;
      var x2 = b.left - g.left,  y2 = b.top + b.height / 2 - g.top;
      var ex = Math.max(x1 + 16, x2 - 24);

      out.push('<path class="br-line br-line--' + l.kind + '" d="M' +
        x1 + ' ' + y1 + ' H' + ex + ' V' + y2 + ' H' + x2 + '"/>');
      out.push('<circle class="br-dot br-dot--' + l.kind + '" cx="' +
        (x2 - 1) + '" cy="' + y2 + '" r="3.5"/>');
      // No text on the paths. Labelling them read well at one width and
      // sat on top of a box at another: the column gap is a fixed 3.6rem,
      // so the only clear space is whatever vertical gap happens to fall
      // beside the elbow, and that moves as the boxes reflow. What the
      // lines mean is said once, in the key underneath, which cannot
      // collide with anything.
    });

    svg.setAttribute("viewBox", "0 0 " + g.width + " " + g.height);
    svg.setAttribute("width", g.width);
    svg.setAttribute("height", g.height);
    svg.innerHTML = out.join("");
  }

  var miRO = null;
  function miWatch() {
    var grid = $("#brGrid");
    if (!grid || typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", miLines);
      return;
    }
    if (miRO) miRO.disconnect();
    miRO = new ResizeObserver(function () { miLines(); });
    miRO.observe(grid);
  }

  function miCopy() {
    var t = MINI.totals, s = MINI.season;
    var title = $("#miniTitle"); if (title) title.textContent = MINI.name;

    var lede = $("#miniLede");
    if (lede) {
      lede.innerHTML =
        'A shorter way to run the league. ' + MINI.pools.length +
        ' pools of ' + MINI.pools[0].teams.length +
        ' play a round robin; the top ' + MINI.pools[0].advance +
        ' of each go through and the last one is out. Then a ' +
        (MINI.pools.length * MINI.pools[0].advance) +
        '-team bracket where a team has to lose <b>twice</b> to be knocked out. ' +
        '<b>' + t.matches + ' matches over ' + t.nights + ' nights</b>' +
        (s ? ', against ' + s.matches + ' matches over ' + s.nights +
             ' nights for the season on the Schedule tab.' : '.');
    }

    /* Derived, not typed. "Proposal — nothing agreed yet" sitting above
       four recorded results is the page contradicting itself, and a
       hand-set flag is exactly how that happens. */
    var pill = $("#miniStatus");
    if (pill) {
      var live = MINI.status === "live";
      var draft = t.played === 0;
      pill.className = "pill" + (draft ? " pill--draft" : "");
      pill.textContent =
        t.played === 0 ? (live ? "Agreed — not started"
                               : "Proposal — nothing agreed yet")
        : t.played < t.matches ? "Under way — " + t.played + " of " +
                                 t.matches + " played"
        : "Finished";
    }

    var note = $("#miniNote");
    if (note) {
      var head = t.played
        ? '<b>These results were reported, not transcribed.</b> A result here ' +
          'names a winner and nothing else — unlike every other number on ' +
          'this site, which is read off a post-game screenshot and ' +
          'checksummed before it counts. Nothing here touches the lobby ' +
          'ledger or the league one, and no player record moves. Post the ' +
          'screenshots in <b>#dota-league-2026</b> and these become real ' +
          'games with the scoreboard behind them. '
        : '<b>Nothing on this tab is a result.</b> It is the format drawn ' +
          'out, not a season that is running: no match here is scheduled and ' +
          'none is recorded. ';
      note.innerHTML = head +
        'The pools, the best-of at each stage, the tie-breaks and the results ' +
        'live in <code>data/mini_tournament.json</code>, and every match, box, ' +
        'placing and count on this page is worked out from them — so no two ' +
        'numbers here can disagree. A pool the results do not separate says ' +
        'so and leaves its bracket slot empty rather than picking somebody.';
    }
  }

  function drawMini() {
    var tab = $("#miniTab"), host = $("#miniBody");
    if (!MINI) {                       // no config, or a config it refused
      if (tab) tab.hidden = true;
      if (host) host.innerHTML = "";
      return;
    }
    if (!host) return;
    miCopy();

    var t = MINI.totals, s = MINI.season;
    var hud =
      '<div class="mc-hud">' +
        '<div class="hud card">' +
          '<div class="hud__cell">' +
            '<div class="hud__label">Teams</div>' +
            '<div class="hud__value">' + t.teams + '</div>' +
            '<div class="hud__sub">' + MINI.pools.map(function (p) {
              return p.teams.length + " in " + p.label; }).join(" · ") + '</div>' +
          '</div>' +
          '<div class="hud__cell">' +
            '<div class="hud__label">Matches</div>' +
            '<div class="hud__value">' + t.matches + '</div>' +
            '<div class="hud__sub">' + t.pool_matches + ' in the pools · ' +
              t.playoff_matches + ' in the bracket</div>' +
          '</div>' +
          '<div class="hud__cell">' +
            '<div class="hud__label">Nights</div>' +
            '<div class="hud__value">' + t.nights + '</div>' +
            '<div class="hud__sub">' + t.slots_per_night +
              ' matches a night' + (s ? ' · the season takes ' + s.nights : '') +
            '</div>' +
          '</div>' +
          '<div class="hud__cell">' +
            '<div class="hud__label">Games</div>' +
            '<div class="hud__value">' + (t.games_min === t.games_max
              ? t.games_min : t.games_min + '–' + t.games_max) + '</div>' +
            '<div class="hud__sub">' + (s ? 'season is ' + s.games_min + '–' +
              s.games_max : '') + (t.games_min === t.games_max
              ? (s ? ' · ' : '') + 'every match a single game' : '') + '</div>' +
          '</div>' +
        '</div>' +
      '</div>';

    var tie = MINI.tie_breaks.length
      ? '<div class="mc-tie card">' +
          '<div class="mc-tie__lbl">If two teams finish level</div>' +
          '<ol class="mc-tie__list">' + MINI.tie_breaks.map(function (x) {
            return '<li>' + esc(x) + '</li>'; }).join("") + '</ol>' +
          '<p class="mc-tie__why">Three teams playing ' +
            miBo(MINI.best_of.pool) + ' each can all finish 1–1, and then the ' +
            'result between two of them settles nothing. That is what the ' +
            'rest of the ladder is for.</p>' +
        '</div>'
      : "";

    var pools =
      '<div class="sec-sub"><h3>The pools</h3>' +
      '<p>Everyone plays everyone inside their own pool. Nobody meets the ' +
      'other pool until the bracket.</p></div>' +
      '<div class="mc-pools">' + MINI.pools.map(miPool).join("") + '</div>' +
      miGroupTable() + tie;

    var bracket =
      '<div class="sec-sub"><h3>The bracket</h3>' +
      '<p>Four teams, and you have to lose twice to go out. The winners of ' +
      'the two pools meet first: the winner of that match is straight into ' +
      'the grand final, the loser drops down with one life left.</p></div>' +
      '<div class="br-scroll">' +
        '<div class="br-grid" id="brGrid">' +
          '<svg class="br-lines" id="brLines" aria-hidden="true" ' +
            'preserveAspectRatio="none"></svg>' +
          MINI.bracket.map(miBox).join("") +
        '</div>' +
      '</div>' +
      '<div class="br-key">' +
        '<span class="br-key__i"><i class="br-key__ln"></i>Winner advances</span>' +
        '<span class="br-key__i is-loss"><i class="br-key__ln"></i>' +
          'Loser drops down a bracket</span>' +
        '<span class="br-hint">Swipe sideways to see the whole bracket.</span>' +
      '</div>';

    var placings =
      '<div class="mc-place card">' +
        '<div class="mc-place__lbl">Where everyone finishes</div>' +
        '<ol class="mc-place__list">' + MINI.placings.map(function (p) {
          return '<li><span class="mc-place__pos">' + esc(p.place) + '</span>' +
            '<span>' + esc(p.from) + '</span></li>'; }).join("") + '</ol>' +
      '</div>';

    host.innerHTML = hud + pools + bracket + placings;
    miLines();
    miWatch();
  }

  /* ── Render ───────────────────────────────────────────────────── */
  function renderAll() {
    applyYear();
    drawYears();
    drawBand();
    drawTally();
    drawStandings();
    drawMatches();
    drawHeroes();
    drawDuos();
    drawTeams();
    drawCoord();
    drawZonePicker();
    drawSchedule();
    drawSeries();
    drawMini();
    var none = cur.matches.length === 0;
    $("#empty").hidden = !none;
    Array.prototype.forEach.call(document.querySelectorAll(".view"), function (v) {
      v.style.display = none ? "none" : "";
    });
  }

  var al = D.meta.aliases || [];
  $("#aliasNote").textContent = al.length
    ? al.length + (al.length === 1 ? " name was" : " names were") + " merged this way."
    : "";

  renderAll();
  wireSort();
  wireTabs();
  fromHash();

  searchBox("qPlayers", "qPlayers", drawStandings);
  searchBox("qHeroes",  "qHeroes",  drawHeroes);
  searchBox("qMatches", "qMatches", drawMatches);
  searchBox("qDuos",    "qDuos",    drawDuos);
  segment("minGames",  "min",  "minGames",  drawStandings, Number);
  segment("heroSort",  "sort", "heroSort",  drawHeroes);
  segment("matchSort", "sort", "matchSort", drawMatches);
  segment("duoSort",   "sort", "duoSort",   drawDuos);
  segment("duoMin",    "min",  "duoMin",    drawDuos, Number);
  segment("fxView", "fxview", "fxView", drawSchedule);
  segment("srView", "srview", "srView", drawSeries);
  segment("tourMin", "min", "tourMin", drawSeries, Number);
  searchBox("qTour", "qTour", drawSeries);
  segment("evidence",  "z", "evidence", function () {
    rescore();
    drawStandings();
    drawDuos();          // the Duos header prints the rating too
    drawSeries();        // ...and so does the tournament board
  }, Number);

  var clr = $("#duoClear");
  if (clr) clr.addEventListener("click", function () {
    state.duoPick = []; state.qDuos = "";
    var q = $("#qDuos"); if (q) q.value = "";
    drawDuos();
  });

  $("#drawerClose").addEventListener("click", closeDrawer);
  scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !drawer.hidden) closeDrawer();
  });
})();
