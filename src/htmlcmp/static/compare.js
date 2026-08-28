"use strict";

const body = document.body;
const PATH = body.dataset.path;
const LIVE = body.dataset.live === "true";

const frameA = document.getElementById("frame-a");
const frameB = document.getElementById("frame-b");

/* ---------------- view mode ---------------- */

function setView(view) {
  body.dataset.view = view;
  for (const b of document.getElementById("views").children) {
    b.classList.toggle("active", b.dataset.view === view);
  }
  localStorage.setItem("htmlcmp.view", view);
}

function setDiffOpen(open) {
  body.dataset.diffOpen = open ? "true" : "false";
  localStorage.setItem("htmlcmp.diffOpen", open ? "true" : "false");
}

document.getElementById("views").addEventListener("click", (event) => {
  const btn = event.target.closest("button");
  if (btn) setView(btn.dataset.view);
});

document.getElementById("toggle-diff").addEventListener("click", () => {
  setDiffOpen(body.dataset.diffOpen !== "true");
});

document.getElementById("expand-diff").addEventListener("click", () => {
  document.getElementById("diff-col").classList.toggle("wide");
});

setView(localStorage.getItem("htmlcmp.view") || "split");
setDiffOpen(localStorage.getItem("htmlcmp.diffOpen") !== "false");

/* ---------------- navigation ---------------- */

const diffsOnlyBox = document.getElementById("diffs-only");
const prevBtn = document.getElementById("prev");
const nextBtn = document.getElementById("next");
const positionEl = document.getElementById("position");

let entries = [];
let prevPath = null;
let nextPath = null;

diffsOnlyBox.checked = localStorage.getItem("htmlcmp.diffsOnly") === "true";
diffsOnlyBox.addEventListener("change", () => {
  localStorage.setItem("htmlcmp.diffsOnly", diffsOnlyBox.checked ? "true" : "false");
  renderNav();
});

function navCandidates() {
  if (!diffsOnlyBox.checked) return entries;
  return entries.filter((e) => e.result !== "same");
}

function renderNav() {
  const candidates = navCandidates();
  prevPath = null;
  nextPath = null;
  for (const e of candidates) {
    if (e.path < PATH) prevPath = e.path;
    else if (e.path > PATH && nextPath === null) nextPath = e.path;
  }
  prevBtn.disabled = prevPath === null;
  nextBtn.disabled = nextPath === null;

  const index = candidates.findIndex((e) => e.path === PATH);
  positionEl.textContent =
    candidates.length === 0
      ? "–"
      : `${index >= 0 ? index + 1 : "–"} / ${candidates.length}`;
}

function goTo(path) {
  if (path) location.href = `/compare/${encodePath(path)}`;
}

prevBtn.addEventListener("click", () => goTo(prevPath));
nextBtn.addEventListener("click", () => goTo(nextPath));

/* ---------------- status ---------------- */

const statusBadge = document.getElementById("status-badge");
const acceptedBadge = document.getElementById("accepted-badge");

function renderStatus(entry) {
  if (entry && entry.result) {
    statusBadge.hidden = false;
    statusBadge.className = `badge ${entry.result}`;
    statusBadge.textContent = entry.message ? `${entry.result} · ${entry.message}` : entry.result;
  } else if (entry && entry.message) {
    statusBadge.hidden = false;
    statusBadge.className = "badge";
    statusBadge.textContent = entry.message;
  } else {
    statusBadge.hidden = true;
  }
  acceptedBadge.hidden = !(entry && entry.accepted);
}

async function refreshEntries() {
  try {
    const data = await fetchEntries();
    entries = data.entries;
  } catch (e) {
    return;
  }
  renderNav();
  renderStatus(entries.find((e) => e.path === PATH));
}

/* ---------------- diff panel ---------------- */

const minimap = document.getElementById("minimap");
const diffImg = document.getElementById("diff-img");
const viewportEl = document.getElementById("viewport");
const regionsEl = document.getElementById("regions");
const statsEl = document.getElementById("diff-stats");
const noteEl = document.getElementById("diff-note");

function percent(fraction) {
  return `${(100 * fraction).toFixed(1)}%`;
}

function showDiffPanel(available, note) {
  minimap.hidden = !available;
  statsEl.hidden = !available;
  noteEl.hidden = available;
  noteEl.textContent = note || "";
}

/* Whether a rendered diff exists can change while the page is open — updating
   the reference creates a file that was missing a moment ago — so the panel
   follows what the endpoint reports rather than the state at render time. */
async function refreshDiffInfo() {
  try {
    const res = await fetch(`/api/diff_info/${encodePath(PATH)}`);
    const info = await res.json();
    if (!res.ok || !info.available) {
      showDiffPanel(false, `no rendered diff: ${info.error || res.statusText}`);
      return;
    }
    showDiffPanel(true);
    regionsEl.replaceChildren();
    if (info.identical) {
      statsEl.textContent = "renders identically";
      return;
    }
    statsEl.innerHTML =
      `first diff at <b>${percent(info.first_diff)}</b><br>` +
      `bounding box <b>${percent(info.area)}</b> of page`;
    for (const [start, end] of info.regions || []) {
      const bar = document.createElement("div");
      bar.className = "region";
      bar.style.top = `${100 * start}%`;
      bar.style.height = `${100 * (end - start)}%`;
      regionsEl.append(bar);
    }
  } catch (e) {
    showDiffPanel(false, "no rendered diff");
  }
}

function reloadDiffImage() {
  const base = diffImg.src.split("?")[0];
  diffImg.src = `${base}?t=${Date.now()}`;
  refreshDiffInfo();
}

/* The diff strip is a full-page render squeezed into the column, so a vertical
   position on it maps onto the documents' scroll height. */
minimap.addEventListener("click", (event) => {
  if (document.getElementById("diff-col").classList.contains("wide")) return;
  const rect = minimap.getBoundingClientRect();
  const fraction = (event.clientY - rect.top) / rect.height;
  for (const frame of [frameA, frameB]) {
    const win = frameWindow(frame);
    if (!win) continue;
    const doc = win.document.documentElement;
    win.scrollTo({ top: fraction * doc.scrollHeight - win.innerHeight / 2 });
  }
});

function updateViewportIndicator() {
  if (!viewportEl) return;
  const win = frameWindow(frameA) || frameWindow(frameB);
  if (!win) return;
  const height = win.document.documentElement.scrollHeight;
  if (!height) return;
  const visible = win.innerHeight / height;
  // Nothing to point at when the whole document fits on screen.
  viewportEl.hidden = visible > 0.98;
  viewportEl.style.top = `${(100 * win.scrollY) / height}%`;
  viewportEl.style.height = `${Math.min(100, 100 * visible)}%`;
}

/* ---------------- scroll sync ---------------- */

function frameWindow(frame) {
  try {
    return frame.contentWindow && frame.contentWindow.document ? frame.contentWindow : null;
  } catch (e) {
    return null; // cross-origin, should not happen for local files
  }
}

/* Whichever pane the user last scrolled drives the other one for a short
   while; that keeps the echo scroll events the sync itself provokes from
   bouncing back and fighting the pane being scrolled. */
let driver = null;
let driverUntil = 0;

function syncFrom(source, target) {
  const now = performance.now();
  if (driver !== source && now < driverUntil) return;

  driver = source;
  driverUntil = now + 100;

  updateViewportIndicator();

  const from = frameWindow(source);
  const to = frameWindow(target);
  if (!from || !to) return;
  if (to.scrollX === from.scrollX && to.scrollY === from.scrollY) return;
  to.scrollTo(from.scrollX, from.scrollY);
}

/* Listeners must be (re-)attached on every load: navigating an iframe replaces
   its window, dropping anything registered on the previous one. */
function attachFrame(frame, other) {
  const win = frameWindow(frame);
  if (!win) return;
  win.addEventListener("scroll", () => syncFrom(frame, other), { passive: true });
  win.document.addEventListener("keydown", onKeyDown);
  updateViewportIndicator();
}

frameA.addEventListener("load", () => attachFrame(frameA, frameB));
frameB.addEventListener("load", () => attachFrame(frameB, frameA));
attachFrame(frameA, frameB);
attachFrame(frameB, frameA);

function reloadFrames() {
  for (const frame of [frameA, frameB]) {
    // eslint-disable-next-line no-self-assign
    frame.src = frame.src;
  }
}

/* ---------------- actions ---------------- */

const updateBtn = document.getElementById("update-ref");

async function doUpdateRef() {
  updateBtn.disabled = true;
  const label = updateBtn.textContent;
  updateBtn.textContent = "Updating…";
  const error = await updateRef(PATH);
  updateBtn.textContent = label;
  updateBtn.disabled = false;
  if (error) {
    showToast(`Update failed: ${error}`, true);
    return;
  }
  showToast("Reference updated — diff accepted");
  acceptedBadge.hidden = false;
  reloadFrames();
  reloadDiffImage();
  refreshEntries();
}

updateBtn.addEventListener("click", doUpdateRef);

/* ---------------- keyboard ---------------- */

const help = document.getElementById("help");
document.getElementById("help-btn").addEventListener("click", () => help.showModal());

function onKeyDown(event) {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const target = event.target;
  if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

  switch (event.key) {
    case "ArrowLeft":
    case "k":
      goTo(prevPath);
      break;
    case "ArrowRight":
    case "j":
      goTo(nextPath);
      break;
    case "1":
      setView("a");
      break;
    case "2":
      setView("split");
      break;
    case "3":
      setView("b");
      break;
    case "d":
      setDiffOpen(body.dataset.diffOpen !== "true");
      break;
    case "u":
      doUpdateRef();
      break;
    case "r":
      reloadFrames();
      reloadDiffImage();
      break;
    case "Escape":
      if (help.open) help.close();
      else location.href = "/";
      break;
    case "?":
      help.open ? help.close() : help.showModal();
      break;
    default:
      return;
  }
  event.preventDefault();
}

document.addEventListener("keydown", onKeyDown);

/* ---------------- boot ---------------- */

refreshEntries();
refreshDiffInfo();
setInterval(updateViewportIndicator, 500);
if (LIVE) setInterval(refreshEntries, 2000);
