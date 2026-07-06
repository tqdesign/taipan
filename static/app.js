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

let sessionId = null;
let prompt = null;       // active prompt descriptor, null while animating
let busy = false;
let pauseTimer = null;
let started = false;

/* ------------------------------------------------------------------ */
/* API */

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

async function newGame() {
  $("log").innerHTML = "";
  const data = await api("/api/new");
  sessionId = data.session_id;
  $("splash").classList.add("hidden");
  $("game").classList.remove("hidden");
  await handleEvent(data.event);
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
  await sleep(70);
}

async function runFx(m) {
  const slots = lorchaSlots();
  const el = m.slot != null ? slots[m.slot] : null;
  switch (m.fx) {
    case "appear":
      if (el) {
        el.textContent = LORCHA.join("\n");
        await sleep(90);
      }
      break;
    case "blast":
      if (el) {
        for (let k = 0; k < 2; k++) {
          el.classList.add("hit");
          el.textContent = BLAST.join("\n");
          await sleep(110);
          el.classList.remove("hit");
          el.textContent = LORCHA.join("\n");
          await sleep(90);
        }
      }
      break;
    case "sink":
      if (el) {
        for (const frame of SINK_FRAMES) {
          el.textContent = frame.join("\n");
          await sleep(160);
        }
        el.textContent = "";
      }
      break;
    case "clear":
      if (el) {
        el.textContent = "";
        await sleep(90);
      }
      break;
    case "incoming": {
      const crt = $("crt");
      crt.classList.add("incoming");
      await sleep(600);
      crt.classList.remove("incoming");
      break;
    }
  }
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
  if (p.hint) $("prompt-hint").textContent = p.hint;

  if (p.kind === "choice") {
    for (const opt of p.options) {
      const btn = keyLabel(opt);
      btn.onclick = () => send(opt.key);
      $("prompt-buttons").appendChild(btn);
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
    if (p.kind === "number" && p.allow_all !== false) {
      const btn = document.createElement("button");
      btn.textContent = "All";
      btn.onclick = () => send("a");
      $("prompt-buttons").appendChild(btn);
    }
  } else if (p.kind === "pause") {
    $("prompt-pause").classList.remove("hidden");
    pauseTimer = setTimeout(() => send(""), p.timeout || 1800);
  } else if (p.kind === "end") {
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
/* Input wiring */

document.addEventListener("keydown", (e) => {
  if (!started) {
    started = true;
    newGame();
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
      send(k);
    } else if (e.key === "Enter") {
      send("");
    }
  } else if (prompt.kind === "number" || prompt.kind === "text") {
    const input = $("prompt-input");
    if (e.key === "Enter") {
      e.preventDefault();
      send(input.value);
    } else if (document.activeElement !== input) {
      input.focus();
    }
  }
});

document.addEventListener("click", () => {
  if (!started) {
    started = true;
    newGame();
    return;
  }
  if (prompt && prompt.kind === "pause" && !busy) send("");
});
