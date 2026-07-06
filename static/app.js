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
const ORDER_KEY = "taipan_last_order";
const ORDER_LABELS = { f: "Fight", r: "Run", t: "Throw cargo" };
const CANCEL = "\x1b";  // must match engine.CANCEL

let sessionId = null;
let prompt = null;       // active prompt descriptor, null while animating
let busy = false;
let pauseTimer = null;
let started = false;
let pendingResume = null;  // {id, event} when a saved voyage can resume

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
  $("options-overlay").classList.remove("hidden");
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

async function newGame(daily = false) {
  $("log").innerHTML = "";
  const data = await api("/api/new", { daily });
  sessionId = data.session_id;
  localStorage.setItem(SESSION_KEY, sessionId);
  $("splash").classList.add("hidden");
  $("game").classList.remove("hidden");
  await handleEvent(data.event);
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

function renderState(st) {
  $("firm").textContent = `${st.firm}, ${st.location}`;
  $("mode-tag").textContent = st.daily ? `DAILY ${st.daily}`
    : (st.mode === "extended" ? "EXTENDED" : "");
  renderMarketLog(st.seen_prices || []);
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

async function showHighscores(wasDaily) {
  try {
    const data = await api("/api/highscores");
    const board = async (title, scores) => {
      if (!scores.length) return;
      await renderMessage({ text: title, cls: "head" });
      for (let i = 0; i < scores.length; i++) {
        const s = scores[i];
        const tag = s.mode === "extended" ? " [ext]" : "";
        await renderMessage({
          text: `${String(i + 1).padStart(2)}. ${s.firm} - `
                + `${s.score.toLocaleString()} (${s.rating}, `
                + `${s.date})${tag}`,
        });
      }
    };
    if (wasDaily) {
      await board(`* * *  TODAY'S CHALLENGE - ${data.daily_date}  * * *`,
                  data.daily_scores);
    }
    await board("* * *  HALL OF FAME  * * *", data.scores);
  } catch (e) { /* scores are optional */ }
}

function renderNetChart(history) {
  if (!history || history.length < 2) return;
  const W = 460, H = 100, PAD = 4;
  const xs = history.map((h) => h[0]);
  const ys = history.map((h) => h[1]);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(0, ...ys), yMax = Math.max(1, ...ys);
  const px = (x) => PAD + ((x - xMin) / Math.max(1, xMax - xMin))
                        * (W - 2 * PAD);
  const py = (y) => H - PAD - ((y - yMin) / (yMax - yMin))
                            * (H - 2 * PAD);
  const pts = history.map((h) => `${px(h[0]).toFixed(1)},`
                                 + `${py(h[1]).toFixed(1)}`).join(" ");
  const zero = py(0);
  const div = document.createElement("div");
  div.className = "line chart";
  div.innerHTML =
    `<div class="chart-title">Net worth over ${xMax} months</div>`
    + `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`
    + `<line x1="0" y1="${zero}" x2="${W}" y2="${zero}"`
    + ` class="chart-zero"/>`
    + `<polyline points="${pts}" class="chart-line"/></svg>`
    + `<div class="chart-title">peak `
    + `${Math.max(...ys).toLocaleString()}</div>`;
  $("log").appendChild(div);
  $("report").scrollTop = $("report").scrollHeight;
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
    renderNetChart(p.net_history);
    showHighscores(!!p.daily);
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
      pendingResume = { id: saved, event: data.event };
      $("resume-hint").classList.remove("hidden");
    } else {
      localStorage.removeItem(SESSION_KEY);
    }
  } catch (e) {
    localStorage.removeItem(SESSION_KEY);
  }
}

function start(key) {
  started = true;
  if (key === "d") {
    pendingResume = null;
    newGame(true);
  } else if (pendingResume && key !== "n") {
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
    if (optionsOpen()) {
      closeOptions();
    } else if (prompt && !busy) {
      if (prompt.cancellable) send(CANCEL);
      else if (prompt.kind === "pause") send("");
    }
    return;
  }
  if (optionsOpen()) return;
  if (!started) {
    start(e.key.toLowerCase());
    return;
  }
  const typing = document.activeElement === $("prompt-input");
  if (e.key.toLowerCase() === "m" && !typing) {
    toggleMute();
    return;
  }
  if (!prompt || busy) return;

  if (prompt.kind === "pause") {
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
  // Clicks on the topbar, the options dialog, or the market-log toggle
  // never reach the game (they must not start it or skip a pause).
  if (e.target.closest("#topbar") || e.target.closest("#options-panel")
      || e.target.closest("#market")) {
    return;
  }
  if (optionsOpen()) {          // clicking the dimmed backdrop closes
    closeOptions();
    return;
  }
  if (!started) {
    start("");
    return;
  }
  if (prompt && prompt.kind === "pause" && !busy) send("");
});

/* Market log UI */
$("market-toggle").addEventListener("click", () => {
  toggleMarket($("market-body").classList.contains("hidden"));
});
toggleMarket(localStorage.getItem(MARKET_KEY) === "1");

/* Options UI */
$("options-btn").addEventListener("click", openOptions);
$("options-close").addEventListener("click", closeOptions);
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
checkResume();
