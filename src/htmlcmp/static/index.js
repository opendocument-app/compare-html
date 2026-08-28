"use strict";

const LIVE = document.body.dataset.live === "true";
const tbody = document.querySelector("tbody");

let activeFilter = "all";

function rowMatchesFilter(tr) {
  if (activeFilter === "all") return true;
  if (activeFilter === "accepted") return tr.dataset.accepted === "true";
  return tr.dataset.status === activeFilter;
}

function applyFilter() {
  const q = document.getElementById("search").value.trim().toLowerCase();
  for (const tr of tbody.rows) {
    const matchesSearch = !q || tr.dataset.path.toLowerCase().includes(q);
    tr.hidden = !(rowMatchesFilter(tr) && matchesSearch);
  }
}

function updateSummary() {
  const counts = { total: 0, same: 0, different: 0, pending: 0, accepted: 0 };
  for (const tr of tbody.rows) {
    counts.total++;
    if (counts[tr.dataset.status] !== undefined) counts[tr.dataset.status]++;
    if (tr.dataset.accepted === "true") counts.accepted++;
  }
  for (const el of document.querySelectorAll("#filters .count")) {
    el.textContent = counts[el.dataset.count];
  }
}

function renderRow(tr, entry) {
  const status = entry.result || "";
  if (tr.dataset.status !== status) {
    tr.dataset.status = status;
    const badge = tr.querySelector(".status .badge:not(.accepted)");
    if (status) {
      if (badge) {
        badge.className = `badge ${status}`;
        badge.textContent = status;
      } else {
        tr.querySelector(".status").insertAdjacentHTML(
          "afterbegin",
          `<span class="badge ${status}"></span>`
        );
        tr.querySelector(".status .badge").textContent = status;
      }
    } else if (badge) {
      badge.remove();
    }
  }
  const accepted = !!entry.accepted;
  if ((tr.dataset.accepted === "true") !== accepted) {
    tr.dataset.accepted = accepted ? "true" : "false";
    tr.querySelector(".status .badge.accepted").hidden = !accepted;
  }
  const message = tr.querySelector(".message");
  if (message.textContent !== (entry.message || "")) {
    message.textContent = entry.message || "";
  }
}

/* Refresh statuses in place; reload only when the set of files changed. */
async function poll() {
  let data;
  try {
    data = await fetchEntries();
  } catch (e) {
    return;
  }
  const known = new Set([...tbody.rows].map((tr) => tr.dataset.path));
  if (data.entries.length !== known.size || data.entries.some((e) => !known.has(e.path))) {
    location.reload();
    return;
  }
  const byPath = new Map(data.entries.map((e) => [e.path, e]));
  for (const tr of tbody.rows) {
    const entry = byPath.get(tr.dataset.path);
    if (entry) renderRow(tr, entry);
  }
  updateSummary();
  applyFilter();
}

async function updateAll() {
  const rows = [...tbody.rows].filter((tr) => !tr.hidden);
  if (rows.length === 0) {
    showToast("No files in the current view.", true);
    return;
  }
  if (!confirm(`Update the reference for ${rows.length} file(s) in the current view?`)) return;

  const btn = document.getElementById("update-all");
  const label = btn.textContent;
  btn.disabled = true;

  const failed = [];
  let done = 0;
  for (const tr of rows) {
    btn.textContent = `Updating ${++done}/${rows.length}…`;
    const error = await updateRef(tr.dataset.path);
    if (error) failed.push(tr.dataset.path);
  }

  btn.disabled = false;
  btn.textContent = label;
  if (failed.length) {
    showToast(`Updated ${rows.length - failed.length}/${rows.length}, ${failed.length} failed.`, true);
  } else {
    showToast(`Updated ${rows.length} reference file(s).`);
  }
  poll();
}

document.getElementById("search").addEventListener("input", applyFilter);

document.getElementById("filters").addEventListener("click", (event) => {
  const btn = event.target.closest("button");
  if (!btn) return;
  activeFilter = btn.dataset.filter;
  for (const b of event.currentTarget.children) b.classList.toggle("active", b === btn);
  applyFilter();
});

document.getElementById("update-all").addEventListener("click", updateAll);

tbody.addEventListener("click", async (event) => {
  const btn = event.target.closest("button[data-action='update-ref']");
  if (!btn) return;
  const tr = btn.closest("tr");
  btn.disabled = true;
  const error = await updateRef(tr.dataset.path);
  btn.disabled = false;
  if (error) {
    showToast(`${tr.dataset.path}: ${error}`, true);
  } else {
    showToast(`Reference updated: ${tr.dataset.path}`);
    poll();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement !== document.getElementById("search")) {
    event.preventDefault();
    document.getElementById("search").focus();
  }
});

updateSummary();
if (LIVE) setInterval(poll, 1500);
