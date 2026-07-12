/* Taipan! web client: drives the game engine over the JSON API. */

"use strict";

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const LORCHA = [
  "-|-_|_  ",
  "-|-_|_  ",
  "_|__|__/",
  "\\_____/ ",
];
const BLAST = ["********", "********", "********", "********"];
// Sinking frames, as in the original: the lorcha slips below the waves.
const SINK_FRAMES = [
  ["        ", "-|-_|_  ", "-|-_|_  ", "_|__|__/"],
  ["        ", "        ", "-|-_|_  ", "-|-_|_  "],
  ["        ", "        ", "        ", "-|-_|_  "],
  ["        ", "        ", "        ", "        "],
];

const SESSION_KEY = "taipan_session";
const MUTE_KEY = "taipan_muted";
const OPTS_KEY = "taipan_opts";
const BEST_KEY = "taipan_best";
const RESTART_KEY = "taipan_restart";
const ORDER_KEY = "taipan_last_order";
const ORDER_LABELS = { f: "Fight", r: "Run", t: "Throw cargo" };
const CANCEL = "\x1b";  // must match engine.CANCEL

let sessionId = null;
let prompt = null;       // active prompt descriptor, null while animating
let busy = false;
let pauseTimer = null;
let started = false;
let pendingResume = null;  // {id, event} when a saved voyage can resume
let challengeId = null;    // set when playing someone's challenge link
let challengeInfo = null;  // {mode, creator: {firm, score, net_history}}

/* ------------------------------------------------------------------ */
/* Options */

let opts = { fast: false, autoOrders: false };
try {
  opts = { ...opts, ...JSON.parse(localStorage.getItem(OPTS_KEY) || "{}") };
} catch (e) { /* corrupted storage; keep defaults */ }

function saveOpts() {
  localStorage.setItem(OPTS_KEY, JSON.stringify(opts));
}

// Effect/animation duration, collapsed in fast play.
function fxd(ms) {
  return opts.fast ? Math.max(15, (ms / 6) | 0) : ms;
}

function optionsOpen() {
  return !$("options-overlay").classList.contains("hidden");
}

function renderOptions() {
  $("opt-fast").checked = opts.fast;
  $("opt-auto").checked = opts.autoOrders;
  $("opt-sound").checked = !muted;
}

function openOptions() {
  renderOptions();
  $("voyage-actions").classList.toggle("hidden",
                                       !(started && sessionId));
  $("options-overlay").classList.remove("hidden");
}

// Destructive buttons ask for a second click within 2.5s.
function armDanger(btn, action) {
  if (btn.dataset.armed === "1") {
    btn.dataset.armed = "";
    action();
    return;
  }
  btn.dataset.armed = "1";
  const label = btn.textContent;
  btn.textContent = "Sure? Click again";
  setTimeout(() => {
    btn.dataset.armed = "";
    btn.textContent = label;
  }, 2500);
}

function closeOptions() {
  $("options-overlay").classList.add("hidden");
}

function isBattleOrders(p) {
  return p && p.kind === "choice"
    && p.options.map((o) => o.key).join("") === "frt";
}

function rememberedOrder() {
  const o = localStorage.getItem(ORDER_KEY);
  return o === "r" ? "r" : "f";   // only Fight/Run auto-repeat
}

function submitChoice(key) {
  // Remember Fight/Run so auto-repeat keeps issuing the same orders.
  // Throw cargo is a one-shot action and is never auto-repeated.
  if (isBattleOrders(prompt) && (key === "f" || key === "r")) {
    localStorage.setItem(ORDER_KEY, key);
  }
  send(key);
}

/* ------------------------------------------------------------------ */
/* Sound (WebAudio, no assets) */

let audioCtx = null;
let muted = localStorage.getItem(MUTE_KEY) === "1";

function ac() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  return audioCtx;
}

function tone(freq, dur, { type = "square", vol = 0.05, when = 0,
                           slide = null } = {}) {
  if (muted) return;
  try {
    const ctx = ac();
    const t0 = ctx.currentTime + when;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (slide) osc.frequency.exponentialRampToValueAtTime(slide, t0 + dur);
    gain.gain.setValueAtTime(vol, t0);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t0);
    osc.stop(t0 + dur + 0.05);
  } catch (e) { /* audio is a garnish; never break the game */ }
}

function noise(dur = 0.2, vol = 0.12, when = 0) {
  if (muted) return;
  try {
    const ctx = ac();
    const buf = ctx.createBuffer(1, ctx.sampleRate * dur, ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) {
      data[i] = (Math.random() * 2 - 1) * (1 - i / data.length);
    }
    const src = ctx.createBufferSource();
    src.buffer = buf;
    const gain = ctx.createGain();
    gain.gain.value = vol;
    src.connect(gain).connect(ctx.destination);
    src.start(ctx.currentTime + when);
  } catch (e) { /* ignore */ }
}

const sfx = {
  shot: () => { tone(880, 0.07, { slide: 180 }); noise(0.05, 0.04); },
  hit: () => noise(0.3, 0.16),
  sink: () => tone(320, 0.8, { type: "sawtooth", vol: 0.06, slide: 55 }),
  alarm: () => {
    tone(660, 0.12);
    tone(440, 0.12, { when: 0.16 });
    tone(660, 0.12, { when: 0.32 });
  },
};

function renderMute() {
  $("mute").textContent = muted ? "[M] sound: OFF" : "[M] sound: ON";
}

function toggleMute() {
  muted = !muted;
  localStorage.setItem(MUTE_KEY, muted ? "1" : "0");
  renderMute();
  if (optionsOpen()) renderOptions();
}

/* ------------------------------------------------------------------ */
/* API */

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

async function newGame(daily = false, challenge = null) {
  $("log").innerHTML = "";
  if (!challenge) {
    challengeId = null;
    challengeInfo = null;
  }
  const data = await api("/api/new", { daily, challenge });
  sessionId = data.session_id;
  localStorage.setItem(SESSION_KEY, sessionId);
  $("splash").classList.add("hidden");
  $("game").classList.remove("hidden");
  if (challenge && challengeInfo) {
    await renderMessage({
      text: `CHALLENGE: beat ${challengeInfo.creator.firm}'s score of `
            + `${challengeInfo.creator.score.toLocaleString()} on the `
            + `same seas. Good joss!`, cls: "head" });
  }
  await handleEvent(data.event);
}

async function fetchChallengeInfo(cid) {
  const res = await fetch(`/api/challenge/${cid}`);
  if (!res.ok) throw new Error("no such challenge");
  return res.json();
}

async function resumeGame() {
  $("log").innerHTML = "";
  sessionId = pendingResume.id;
  const ev = pendingResume.event;
  pendingResume = null;
  $("splash").classList.add("hidden");
  $("game").classList.remove("hidden");
  await renderMessage({ text: "Welcome back, Taipan. Resuming your "
                              + "voyage...", cls: "head" });
  await handleEvent(ev);
}

async function send(value) {
  if (busy || !sessionId) return;
  busy = true;
  prompt = null;
  clearPause();
  hidePromptUI();
  try {
    const data = await api("/api/step", { session_id: sessionId, value });
    await handleEvent(data.event);
  } finally {
    busy = false;
  }
}

/* ------------------------------------------------------------------ */
/* Event handling */

async function handleEvent(ev) {
  renderState(ev.state);
  renderBattle(ev.battle, false);
  for (const m of ev.messages) {
    await renderMessage(m);
  }
  renderBattle(ev.battle, true);
  showPrompt(ev.prompt);
}

function loadBest() {
  try {
    return JSON.parse(localStorage.getItem(BEST_KEY));
  } catch (e) {
    return null;
  }
}

function renderPace(st) {
  const el = $("pace");
  // In a challenge, race the challenger's ghost; otherwise your best.
  let curve, label;
  if (challengeInfo && challengeInfo.creator.net_history.length) {
    curve = challengeInfo.creator.net_history;
    label = `${challengeInfo.creator.firm}'s pace (score `
      + `${challengeInfo.creator.score.toLocaleString()})`;
  } else {
    const best = loadBest();
    if (!best || !best.net_history) {
      el.classList.add("hidden");
      return;
    }
    curve = best.net_history;
    label = `Record pace (score ${best.score.toLocaleString()})`;
  }
  if (st.time == null) {
    el.classList.add("hidden");
    return;
  }
  let ghostNet = null;
  for (const [t, net] of curve) {
    if (t <= st.time) ghostNet = net;
    else break;
  }
  if (ghostNet === null) {
    el.classList.add("hidden");
    return;
  }
  const diff = st.net - ghostNet;
  el.textContent = `${label}: ${Math.abs(diff).toLocaleString()} `
    + `${diff >= 0 ? "AHEAD" : "behind"}`;
  el.classList.remove("hidden");
}

let lastFirm = "Taipan";
let lastState = null;

function renderState(st) {
  lastFirm = st.firm;
  lastState = st;
  $("firm").textContent = `${st.firm}, ${st.location}`;
  $("mode-tag").textContent = challengeId ? "CHALLENGE"
    : st.daily ? `DAILY ${st.daily}`
    : (st.mode === "extended" ? "EXTENDED" : "");
  renderMarketLog(st.seen_prices || []);
  renderPace(st);
  const venture = $("venture");
  const bits = [];
  if (st.charter) bits.push(`<span class="charter">Charter: `
                            + `${st.charter}</span>`);
  if (st.refits && st.refits.length) {
    bits.push(`Refits: ${st.refits.join(", ")}`);
  }
  venture.innerHTML = bits.join(" &nbsp;&middot;&nbsp; ");
  venture.classList.toggle("hidden", bits.length === 0);
  for (let i = 0; i < 4; i++) {
    $(`wh-${i}`).textContent = st.warehouse[i].toLocaleString();
    $(`hold-${i}`).textContent = st.hold_items[i].toLocaleString();
    const p = $(`price-${i}`);
    p.textContent = st.prices ? st.prices[i].toLocaleString() : "-";
  }
  $("wh-used").textContent = st.warehouse_used.toLocaleString();
  $("wh-vacant").textContent = st.warehouse_vacant.toLocaleString();
  $("hold-space").textContent = st.overloaded ? "Overload" : st.hold_space;
  $("guns").textContent = st.guns;
  $("date").textContent = `15 ${st.month} ${st.year}`;
  $("location").textContent = st.destination
    ? `At sea (to ${st.destination})` : st.location;
  $("debt").textContent = st.debt_str;
  $("status").textContent = `${st.status_label}: ${st.status_pct}`;
  $("cash").textContent = st.cash_str;
  $("bank").textContent = st.bank_str;
  $("prices").classList.toggle("hidden", !st.prices);
}

/* ------------------------------------------------------------------ */
/* Market log: last prices seen per port */

const MARKET_KEY = "taipan_market_open";

function renderMarketLog(seen) {
  const table = $("market-table");
  table.innerHTML = "";
  if (!seen.length) {
    table.innerHTML = "<tr><td>No ports visited yet.</td></tr>";
    return;
  }
  const head = table.insertRow();
  for (const h of ["Port", "Opium", "Silk", "Arms", "General", "Seen"]) {
    const td = head.insertCell();
    td.textContent = h;
    td.className = h === "Port" || h === "Seen" ? "" : "num";
  }
  head.className = "market-head";
  for (const s of seen) {
    const row = table.insertRow();
    if (s.here) row.className = "market-here";
    row.insertCell().textContent = s.port + (s.here ? " *" : "");
    for (const p of s.prices) {
      const td = row.insertCell();
      td.textContent = p.toLocaleString();
      td.className = "num";
    }
    row.insertCell().textContent = s.when;
  }
}

function toggleMarket(open) {
  $("market-body").classList.toggle("hidden", !open);
  $("market-arrow").textContent = open ? "[-]" : "[+]";
  localStorage.setItem(MARKET_KEY, open ? "1" : "0");
}

/* ------------------------------------------------------------------ */
/* Battle screen */

function lorchaSlots() {
  const grid = $("lorchas");
  if (grid.children.length === 0) {
    for (let i = 0; i < 10; i++) {
      const pre = document.createElement("pre");
      pre.className = "lorcha";
      pre.textContent = "";
      grid.appendChild(pre);
    }
  }
  return grid.children;
}

function renderBattle(b, syncSlots) {
  const battleEl = $("battle");
  if (!b) {
    battleEl.classList.add("hidden");
    $("lorchas").innerHTML = "";
    return;
  }
  battleEl.classList.remove("hidden");
  $("b-ships").textContent = b.ships;
  $("b-plural").textContent = b.ships === 1 ? "" : "s";
  $("b-guns").textContent = b.guns;
  $("b-guns-plural").textContent = b.guns === 1 ? "" : "s";
  $("b-orders").textContent = b.orders || "";
  $("b-seaworthy").textContent = `${b.status_label} (${b.status_pct}%)`;
  $("b-more").classList.toggle("hidden", !b.more);
  if (syncSlots) {
    const slots = lorchaSlots();
    for (let i = 0; i < 10; i++) {
      slots[i].textContent = b.slots[i] ? LORCHA.join("\n") : "";
      slots[i].classList.remove("hit");
    }
  }
}

async function renderMessage(m) {
  if (m.fx) return runFx(m);
  const div = document.createElement("div");
  div.className = `line ${m.cls || ""}`;
  m.text.split("\n").forEach((t, i) => {
    if (i > 0) div.appendChild(document.createElement("br"));
    div.appendChild(document.createTextNode(t));
  });
  const log = $("log");
  log.appendChild(div);
  while (log.children.length > 250) log.removeChild(log.firstChild);
  $("report").scrollTop = $("report").scrollHeight;
  if (!opts.fast) await sleep(70);
}

async function runFx(m) {
  const slots = lorchaSlots();
  const el = m.slot != null ? slots[m.slot] : null;
  switch (m.fx) {
    case "appear":
      if (el) {
        el.textContent = LORCHA.join("\n");
        await sleep(fxd(90));
      }
      break;
    case "blast":
      if (el) {
        sfx.shot();
        for (let k = 0; k < 2; k++) {
          el.classList.add("hit");
          el.textContent = BLAST.join("\n");
          await sleep(fxd(110));
          el.classList.remove("hit");
          el.textContent = LORCHA.join("\n");
          await sleep(fxd(90));
        }
      }
      break;
    case "sink":
      if (el) {
        sfx.sink();
        for (const frame of SINK_FRAMES) {
          el.textContent = frame.join("\n");
          await sleep(fxd(160));
        }
        el.textContent = "";
      }
      break;
    case "clear":
      if (el) {
        el.textContent = "";
        await sleep(fxd(90));
      }
      break;
    case "incoming": {
      sfx.alarm();
      const crt = $("crt");
      crt.classList.add("incoming");
      await sleep(fxd(450));
      sfx.hit();
      await sleep(fxd(150));
      crt.classList.remove("incoming");
      break;
    }
  }
}

/* ------------------------------------------------------------------ */
/* High scores */

function scoresOpen() {
  return !$("scores-overlay").classList.contains("hidden");
}

function closeScores() {
  $("scores-overlay").classList.add("hidden");
}

function helpOpen() {
  return !$("help-overlay").classList.contains("hidden");
}

function closeHelp() {
  $("help-overlay").classList.add("hidden");
}

function scoreLine(s, i) {
  const tag = s.mode === "extended" ? " [ext]" : "";
  return `${String(i + 1).padStart(2)}. ${s.firm} - `
       + `${s.score.toLocaleString()} (${s.rating}, ${s.date})${tag}`;
}

async function openScores() {
  let data;
  try {
    data = await api("/api/highscores");
  } catch (e) {
    return;
  }
  const body = $("scores-body");
  body.innerHTML = "";
  const board = (title, scores) => {
    const h = document.createElement("div");
    h.className = "board-title";
    h.textContent = title;
    body.appendChild(h);
    if (!scores.length) {
      const d = document.createElement("div");
      d.className = "line empty";
      d.textContent = "No entries yet. The seas await, Taipan.";
      body.appendChild(d);
      return;
    }
    scores.forEach((s, i) => {
      const d = document.createElement("div");
      d.className = "line";
      d.textContent = scoreLine(s, i);
      body.appendChild(d);
    });
  };
  board(`Today's Challenge - ${data.daily_date}`, data.daily_scores);
  board("All-Time", data.scores);
  const ach = Object.entries(data.achievements || {});
  if (ach.length) {
    const h = document.createElement("div");
    h.className = "board-title";
    h.textContent = "Honors Unlocked";
    body.appendChild(h);
    for (const [, a] of ach) {
      const d = document.createElement("div");
      d.className = "line";
      d.textContent = `* ${a.name} - ${a.desc} (first: ${a.firm})`;
      body.appendChild(d);
    }
  }
  $("scores-overlay").classList.remove("hidden");
}

async function showChallengeBoard(myScore) {
  try {
    const info = await fetchChallengeInfo(challengeId);
    const c = info.creator;
    await renderMessage({ text: "* * *  CHALLENGE BOARD  * * *",
                          cls: "head" });
    const beat = myScore > c.score;
    await renderMessage({
      text: beat
        ? `You BEAT ${c.firm}'s ${c.score.toLocaleString()}! `
          + `The seas are yours, Taipan!`
        : `${c.firm}'s ${c.score.toLocaleString()} stands. `
          + `Their ghost still laughs.`,
      cls: beat ? "big" : "normal" });
    const entries = [{ firm: `${c.firm} (challenger)`, score: c.score,
                       rating: c.rating }].concat(info.attempts);
    entries.sort((a, b) => b.score - a.score);
    for (let i = 0; i < Math.min(entries.length, 10); i++) {
      const s = entries[i];
      const tries = s.attempt > 1 ? `, try #${s.attempt}` : "";
      await renderMessage({
        text: `${String(i + 1).padStart(2)}. ${s.firm} - `
              + `${s.score.toLocaleString()} (${s.rating}${tries})` });
    }
  } catch (e) { /* board is garnish */ }
}

async function showHighscores(wasDaily) {
  try {
    const data = await api("/api/highscores");
    const board = async (title, scores) => {
      if (!scores.length) return;
      await renderMessage({ text: title, cls: "head" });
      for (let i = 0; i < scores.length; i++) {
        await renderMessage({ text: scoreLine(scores[i], i) });
      }
    };
    if (wasDaily) {
      await board(`* * *  TODAY'S CHALLENGE - ${data.daily_date}  * * *`,
                  data.daily_scores);
    }
    await board("* * *  HALL OF FAME  * * *", data.scores);
  } catch (e) { /* scores are optional */ }
}

function renderNetChart(history, ghost) {
  if (!history || history.length < 2) return;
  const W = 460, H = 100, PAD = 4;
  const all = ghost ? history.concat(ghost) : history;
  const xMin = Math.min(...all.map((h) => h[0]));
  const xMax = Math.max(...all.map((h) => h[0]));
  const yMin = Math.min(0, ...all.map((h) => h[1]));
  const yMax = Math.max(1, ...all.map((h) => h[1]));
  const px = (x) => PAD + ((x - xMin) / Math.max(1, xMax - xMin))
                        * (W - 2 * PAD);
  const py = (y) => H - PAD - ((y - yMin) / (yMax - yMin))
                            * (H - 2 * PAD);
  const line = (h) => h.map((p) => `${px(p[0]).toFixed(1)},`
                                   + `${py(p[1]).toFixed(1)}`).join(" ");
  const zero = py(0);
  const div = document.createElement("div");
  div.className = "line chart";
  div.innerHTML =
    `<div class="chart-title">Net worth over `
    + `${Math.max(...history.map((h) => h[0]))} months`
    + `${ghost ? " (dim line: your best run)" : ""}</div>`
    + `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`
    + `<line x1="0" y1="${zero}" x2="${W}" y2="${zero}"`
    + ` class="chart-zero"/>`
    + (ghost ? `<polyline points="${line(ghost)}" class="chart-ghost"/>`
             : "")
    + `<polyline points="${line(history)}" class="chart-line"/></svg>`
    + `<div class="chart-title">peak `
    + `${Math.max(...history.map((h) => h[1])).toLocaleString()}</div>`;
  $("log").appendChild(div);
  $("report").scrollTop = $("report").scrollHeight;
}

/* ------------------------------------------------------------------ */
/* Captain's log */

function journalOpen() {
  return !$("journal-overlay").classList.contains("hidden");
}

function closeJournal() {
  $("journal-overlay").classList.add("hidden");
}

let journalText = "";

function openJournal(journal, header) {
  const body = $("journal-body");
  body.innerHTML = "";
  const lines = [header];
  const hd = document.createElement("div");
  hd.className = "line head";
  hd.textContent = header;
  body.appendChild(hd);
  for (const e of journal || []) {
    const div = document.createElement("div");
    div.className = "line";
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = e.when;
    div.appendChild(when);
    div.appendChild(document.createTextNode(e.text));
    body.appendChild(div);
    lines.push(`${e.when} - ${e.text}`);
  }
  if (!(journal || []).length) {
    const div = document.createElement("div");
    div.className = "line";
    div.textContent = "An uneventful career. The sea kept its stories.";
    body.appendChild(div);
  }
  journalText = lines.join("\n");
  $("journal-overlay").classList.remove("hidden");
}

/* ------------------------------------------------------------------ */
/* Prompts */

function hidePromptUI() {
  $("prompt-text").textContent = "";
  $("prompt-hint").textContent = "";
  $("prompt-buttons").innerHTML = "";
  $("prompt-entry").classList.add("hidden");
  $("prompt-pause").classList.add("hidden");
}

function clearPause() {
  if (pauseTimer) {
    clearTimeout(pauseTimer);
    pauseTimer = null;
  }
}

function confirmAck() {
  $("ack-overlay").classList.add("hidden");
  send("");
}

function keyLabel(opt) {
  // Underline the option's hotkey inside its label where possible.
  const { key, label } = opt;
  const idx = label.toLowerCase().indexOf(key);
  const btn = document.createElement("button");
  if (key.length === 1 && idx >= 0) {
    btn.append(
      label.slice(0, idx),
      Object.assign(document.createElement("span"),
        { className: "key", textContent: label[idx] }),
      label.slice(idx + 1));
  } else {
    btn.append(`${key}) ${label}`);
  }
  if (opt.danger) btn.classList.add("danger");
  return btn;
}

function showPrompt(p) {
  prompt = p;
  hidePromptUI();
  $("prompt-text").textContent = p.text || "";
  const hints = [];
  if (p.hint) hints.push(p.hint);
  if (p.cancellable) hints.push("(Esc cancels)");
  if (hints.length) $("prompt-hint").textContent = hints.join("   ");

  if (p.kind === "choice") {
    for (const opt of p.options) {
      const btn = keyLabel(opt);
      btn.onclick = () => submitChoice(opt.key);
      $("prompt-buttons").appendChild(btn);
    }
    if (isBattleOrders(p) && opts.autoOrders) {
      const order = rememberedOrder();
      $("prompt-hint").textContent =
        `auto: ${ORDER_LABELS[order]} - press F/R/T to change`;
      pauseTimer = setTimeout(() => send(order),
                              opts.fast ? 250 : 1000);
    }
  } else if (p.kind === "number" || p.kind === "text") {
    const entry = $("prompt-entry");
    const input = $("prompt-input");
    entry.classList.remove("hidden");
    input.value = "";
    input.maxLength = p.kind === "text" ? (p.maxlen || 22) : 10;
    input.placeholder = p.kind === "number"
      ? (p.allow_all !== false ? "amount, or A for all" : "amount") : "";
    input.focus();
    if (p.kind === "number") {
      for (const pre of p.presets || []) {
        const btn = document.createElement("button");
        btn.textContent = pre.label;
        btn.title = pre.value.toLocaleString();
        btn.onclick = () => send(String(pre.value));
        $("prompt-buttons").appendChild(btn);
      }
      if (p.allow_all !== false) {
        const btn = document.createElement("button");
        btn.textContent = "All";
        btn.onclick = () => send("a");
        $("prompt-buttons").appendChild(btn);
      }
    }
  } else if (p.kind === "pause") {
    $("prompt-pause").classList.remove("hidden");
    pauseTimer = setTimeout(() => send(""),
                            opts.fast ? 40 : (p.timeout || 1800));
  } else if (p.kind === "ack") {
    // Bad Joss: a loss the player must explicitly acknowledge.
    // Never auto-advanced, even in fast play.
    const box = $("ack-lines");
    box.innerHTML = "";
    for (const ln of p.lines || []) {
      const div = document.createElement("div");
      div.className = `line ${ln.cls || ""}`;
      div.textContent = ln.text;
      box.appendChild(div);
    }
    $("ack-overlay").classList.remove("hidden");
    $("ack-ok").focus();
  } else if (p.kind === "end") {
    /* handled below */
  }

  if (p.cancellable) {
    const btn = document.createElement("button");
    btn.className = "cancel";
    btn.textContent = "Cancel (Esc)";
    btn.onclick = () => send(CANCEL);
    $("prompt-buttons").appendChild(btn);
  }

  if (p.kind === "end") {
    localStorage.removeItem(SESSION_KEY);
    const best = loadBest();
    const ghost = (challengeInfo
                   && challengeInfo.creator.net_history.length)
      ? challengeInfo.creator.net_history
      : (best && best.net_history ? best.net_history : null);
    renderNetChart(p.net_history, ghost);
    if (!best || p.score > best.score) {
      try {
        localStorage.setItem(BEST_KEY, JSON.stringify(
          { score: p.score, net_history: p.net_history }));
      } catch (e) { /* storage full; the ghost can wait */ }
    }
    if (challengeId) {
      showChallengeBoard(p.score);
    }
    showHighscores(!!p.daily);
    const shareBtn = document.createElement("button");
    shareBtn.textContent = "Challenge a friend";
    shareBtn.onclick = async () => {
      try {
        const d = await api("/api/challenge",
                            { session_id: sessionId });
        const url = `${location.origin}/?challenge=${d.challenge_id}`;
        await navigator.clipboard.writeText(url);
        shareBtn.textContent = "Link copied!";
      } catch (e) {
        shareBtn.textContent = "Failed - try again";
      }
      setTimeout(() => {
        shareBtn.textContent = "Challenge a friend";
      }, 2000);
    };
    $("prompt-buttons").appendChild(shareBtn);
    const logBtn = document.createElement("button");
    logBtn.textContent = "Captain's Log";
    logBtn.onclick = () => openJournal(
      p.journal,
      `The voyages of ${lastFirm} - score ${p.score.toLocaleString()} `
      + `(${p.rating})`);
    $("prompt-buttons").appendChild(logBtn);
    if (challengeId) {
      const retryBtn = document.createElement("button");
      retryBtn.textContent = "Retry challenge";
      const cid = challengeId;
      retryBtn.onclick = () => {
        sessionId = null;
        prompt = null;
        newGame(false, cid);
      };
      $("prompt-buttons").appendChild(retryBtn);
    }
    const btn = document.createElement("button");
    btn.textContent = "Play again";
    btn.onclick = () => {
      sessionId = null;
      prompt = null;
      newGame();
    };
    $("prompt-buttons").appendChild(btn);
  }
}

/* ------------------------------------------------------------------ */
/* Startup / resume */

async function checkResume() {
  const saved = localStorage.getItem(SESSION_KEY);
  if (!saved) return;
  try {
    const data = await api(`/api/state/${saved}`);
    if (data.event && !data.event.done) {
      pendingResume = { id: saved, event: data.event,
                        challenge: data.challenge };
      $("resume-hint").classList.remove("hidden");
    } else {
      localStorage.removeItem(SESSION_KEY);
    }
  } catch (e) {
    localStorage.removeItem(SESSION_KEY);
  }
}

async function checkChallengeLink() {
  const cid = new URLSearchParams(location.search).get("challenge");
  if (!cid) return;
  try {
    challengeInfo = await fetchChallengeInfo(cid);
    challengeId = cid;
    const c = challengeInfo.creator;
    $("challenge-hint").textContent =
      `A CHALLENGE from ${c.firm} (score `
      + `${c.score.toLocaleString()}, ${c.rating}) - any key sails `
      + `the same seas against their ghost`;
    $("challenge-hint").classList.remove("hidden");
  } catch (e) {
    challengeId = null;
  }
}

function start(key) {
  started = true;
  if (challengeId && key !== "n") {
    pendingResume = null;
    newGame(false, challengeId);
  } else if (key === "d") {
    pendingResume = null;
    newGame(true);
  } else if (pendingResume && key !== "n") {
    if (pendingResume.challenge) {
      challengeId = pendingResume.challenge;
      fetchChallengeInfo(challengeId)
        .then((info) => { challengeInfo = info; })
        .catch(() => {});
    }
    resumeGame();
  } else {
    pendingResume = null;
    newGame();
  }
}

/* ------------------------------------------------------------------ */
/* Input wiring */

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (helpOpen()) {
      closeHelp();
    } else if (journalOpen()) {
      closeJournal();
    } else if (scoresOpen()) {
      closeScores();
    } else if (optionsOpen()) {
      closeOptions();
    } else if (prompt && !busy) {
      if (prompt.cancellable) send(CANCEL);
      else if (prompt.kind === "pause") send("");
    }
    return;
  }
  if (optionsOpen() || scoresOpen() || journalOpen() || helpOpen()) {
    return;
  }
  if (!started) {
    if (e.key.toLowerCase() === "h") {   // How to Play, without starting
      $("help-overlay").classList.remove("hidden");
      return;
    }
    start(e.key.toLowerCase());
    return;
  }
  const typing = document.activeElement === $("prompt-input");
  if (e.key.toLowerCase() === "m" && !typing) {
    toggleMute();
    return;
  }
  if (!prompt || busy) return;

  if (prompt.kind === "ack") {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      confirmAck();
    }
  } else if (prompt.kind === "pause") {
    e.preventDefault();
    send("");
  } else if (prompt.kind === "choice") {
    const k = e.key.toLowerCase();
    if (prompt.options.some((o) => o.key === k)) {
      e.preventDefault();
      submitChoice(k);
    } else if (e.key === "Enter") {
      send("");
    }
  } else if (prompt.kind === "number" || prompt.kind === "text") {
    const input = $("prompt-input");
    if (e.key === "Enter") {
      e.preventDefault();
      send(input.value);
    } else if (!typing) {
      input.focus();
    }
  }
});

document.addEventListener("click", (e) => {
  // Clicks on the topbar, dialogs, or the market-log toggle never
  // reach the game (they must not start it or skip a pause).
  if (e.target.closest("#topbar") || e.target.closest("#options-panel")
      || e.target.closest("#market") || e.target.closest("#ack-panel")
      || e.target.closest("#scores-panel")
      || e.target.closest("#journal-panel")
      || e.target.closest("#help-panel")
      || e.target.closest("#splash-help")) {
    return;
  }
  if (helpOpen()) {             // clicking the dimmed backdrop closes
    closeHelp();
    return;
  }
  if (journalOpen()) {
    closeJournal();
    return;
  }
  if (scoresOpen()) {
    closeScores();
    return;
  }
  if (optionsOpen()) {
    closeOptions();
    return;
  }
  if (!started) {
    start("");
    return;
  }
  if (prompt && prompt.kind === "pause" && !busy) send("");
});

$("ack-ok").addEventListener("click", confirmAck);

/* Hall of Fame UI */
$("scores-btn").addEventListener("click", openScores);
$("scores-close").addEventListener("click", closeScores);

/* Text/number entry submit button */
$("prompt-enter").addEventListener("click", () => {
  if (prompt && (prompt.kind === "number" || prompt.kind === "text")) {
    send($("prompt-input").value);
  }
});

/* How to play UI */
$("help-btn").addEventListener("click", () => {
  $("help-overlay").classList.remove("hidden");
});
$("splash-help").addEventListener("click", () => {
  $("help-overlay").classList.remove("hidden");
});
$("help-close").addEventListener("click", closeHelp);

/* Captain's log UI */
$("journal-close").addEventListener("click", closeJournal);
$("journal-copy").addEventListener("click", () => {
  navigator.clipboard.writeText(journalText).then(() => {
    $("journal-copy").textContent = "Copied!";
    setTimeout(() => { $("journal-copy").textContent = "Copy"; }, 1500);
  }).catch(() => {});
});

/* Market log UI */
$("market-toggle").addEventListener("click", () => {
  toggleMarket($("market-body").classList.contains("hidden"));
});
toggleMarket(localStorage.getItem(MARKET_KEY) === "1");

/* Options UI */
$("options-btn").addEventListener("click", openOptions);
$("options-close").addEventListener("click", closeOptions);
$("restart-btn").addEventListener("click", (e) => {
  armDanger(e.target, () => {
    // Same seas type, fresh start: keep daily/challenge context.
    localStorage.removeItem(SESSION_KEY);
    const ctx = {};
    if (lastState && lastState.daily) ctx.daily = true;
    if (challengeId) ctx.challenge = challengeId;
    localStorage.setItem(RESTART_KEY, JSON.stringify(ctx));
    location.reload();   // reload clears every in-flight timer cleanly
  });
});
$("quit-btn").addEventListener("click", (e) => {
  armDanger(e.target, () => {
    localStorage.removeItem(SESSION_KEY);
    location.reload();
  });
});
$("mute").addEventListener("click", toggleMute);
$("opt-fast").addEventListener("change", (e) => {
  opts.fast = e.target.checked;
  saveOpts();
  // A pause may be counting down with the old delay; re-arm it.
  if (prompt && prompt.kind === "pause" && !busy) {
    clearPause();
    pauseTimer = setTimeout(() => send(""), opts.fast ? 40 : 800);
  }
});
$("opt-auto").addEventListener("change", (e) => {
  opts.autoOrders = e.target.checked;
  saveOpts();
});
$("opt-sound").addEventListener("change", (e) => {
  muted = !e.target.checked;
  localStorage.setItem(MUTE_KEY, muted ? "1" : "0");
  renderMute();
});

renderMute();
api("/api/version")
  .then((d) => { $("version").textContent = d.version; })
  .catch(() => {});

// A restart requested from the options dialog skips the splash and
// starts a fresh game in the same context (daily/challenge/normal).
const restartCtx = localStorage.getItem(RESTART_KEY);
if (restartCtx) {
  localStorage.removeItem(RESTART_KEY);
  let ctx = {};
  try {
    ctx = JSON.parse(restartCtx);
  } catch (e) { /* fall through to a plain new game */ }
  started = true;
  if (ctx.challenge) {
    fetchChallengeInfo(ctx.challenge)
      .then((info) => {
        challengeInfo = info;
        challengeId = ctx.challenge;
        newGame(false, ctx.challenge);
      })
      .catch(() => newGame());
  } else {
    newGame(!!ctx.daily);
  }
} else {
  checkResume();
  checkChallengeLink();
}
