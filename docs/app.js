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
    evidence: 2.576
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
    var together = rec(), split = rec(), perSolo = {};
    names.forEach(function (n) { perSolo[n] = rec(); });
    cur.matches.forEach(function (m) {
      var sides = names.map(function (n) { return sideOf(m, n); });
      var present = sides.filter(Boolean);
      if (!present.length) return;
      var all = present.length === names.length;
      var same = all && present.every(function (s) { return s === present[0]; });
      if (same) {
        add(together, m.winning_side === present[0]);
      } else if (present.length > 1 && !present.every(function (s) { return s === present[0]; })) {
        split.g++;   // they were on opposing sides; nobody "wins" as a group
      }
      // Each member's record in games where the full squad was NOT together.
      names.forEach(function (n, i) {
        if (sides[i] && !same) add(perSolo[n], m.winning_side === sides[i]);
      });
    });
    return { together: together, split: split, apart: perSolo };
  }

  // countOnly: for records that have no win rate at all. A game where the
  // selection was split across both teams produced a winner AND a loser
  // among them, so the group neither won nor lost it. Rendering that as
  // "0%" beside "0W 0L" reads as "this squad always loses", which is the
  // opposite of true — those games simply do not belong to the squad.
  function statCard(label, r, hint, countOnly) {
    var p = pctOf(r);
    var big = countOnly
      ? r.g + '<i>' + (r.g === 1 ? " game" : " games") + "</i>"
      : (p === null ? "—" : p.toFixed(0) + "<i>%</i>");
    var sub = countOnly
      ? "neither won nor lost by the group"
      : r.g + (r.g === 1 ? " game · " : " games · ") +
        "<b>" + r.w + "W</b> <i>" + r.l + "L</i>";
    return '<div class="duo-stat">' +
      '<p class="duo-stat__label">' + esc(label) + "</p>" +
      '<p class="duo-stat__big">' + big + "</p>" +
      '<p class="duo-stat__sub">' + sub + "</p>" +
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
      h += '<div class="duo-stats">' +
        statCard("On the same team", sq.together, "all " + sel.length + " together") +
        statCard("Split up", sq.split, "they were on opposing sides", true) +
        "</div>";

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
  segment("evidence",  "z", "evidence", function () {
    rescore();
    drawStandings();
    drawDuos();          // the Duos header prints the rating too
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
