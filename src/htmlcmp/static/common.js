"use strict";

/* Helpers shared by the index and compare pages. */

function encodePath(path) {
  return path.split("/").map(encodeURIComponent).join("/");
}

let toastTimer = null;

function showToast(message, isError) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.classList.toggle("error", !!isError);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

/* Copy the monitored (B) file over the reference (A) file.
   Resolves to an error message, or null on success. */
async function updateRef(path) {
  try {
    const res = await fetch(`/api/update_ref/${encodePath(path)}`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return data.error || res.statusText || "request failed";
    return null;
  } catch (e) {
    return String(e);
  }
}

async function fetchEntries() {
  const res = await fetch("/api/entries");
  if (!res.ok) throw new Error(res.statusText);
  return await res.json();
}
