#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import sys
import shutil
import argparse
import logging
import threading
import functools
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, send_from_directory, send_file
import watchdog.observers
import watchdog.events

from htmlcmp.compare_output import comparable_file, compare_files
from htmlcmp.html_render_diff import get_browser, html_render_diff

logger = logging.getLogger(__name__)


class Config:
    path_a: Path = None
    path_b: Path = None
    driver: str = None
    observer = None
    comparator = None
    browser = None
    thread_local = threading.local()
    log_file: Path = None


def result_symbol(result: str) -> str | None:
    if not isinstance(result, str):
        raise TypeError("Result must be of type str")

    if result == "pending":
        return "🔄"
    if result == "same":
        return "✔"
    if result == "different":
        return "❌"
    return None


class Observer:
    def __init__(self):
        class Handler(watchdog.events.FileSystemEventHandler):
            def __init__(self, path):
                self._path = path

            def dispatch(self, event):
                event_type = event.event_type
                src_path = Path(event.src_path)

                logger.debug(f"Watchdog event: {event_type} {src_path}")

                if event_type not in ["moved", "deleted", "created", "modified"]:
                    return

                if src_path.is_file():
                    logger.debug(
                        f"Submit watchdog file change: {event_type} {src_path}"
                    )
                    Config.comparator.submit(src_path.relative_to(self._path))

        logger.info("Create watchdog for paths:")
        logger.info(f"  A: {Config.path_a}")
        logger.info(f"  B: {Config.path_b}")

        self._observer = watchdog.observers.Observer()
        self._observer.schedule(Handler(Config.path_a), Config.path_a, recursive=True)
        self._observer.schedule(Handler(Config.path_b), Config.path_b, recursive=True)

    def start(self) -> None:
        logger.info("Starting watchdog observer")
        self._observer.start()

        def init_compare(a: Path, b: Path):
            logger.debug(f"Initial compare: {a} vs {b}")

            if not isinstance(a, Path) or not isinstance(b, Path):
                raise TypeError("Paths must be of type Path")
            if not a.is_dir() or not b.is_dir():
                raise ValueError("Both paths must be directories")

            common_path = a.relative_to(Config.path_a)

            left = sorted(p.name for p in a.iterdir())
            right = sorted(p.name for p in b.iterdir())

            common = [name for name in left if name in right]

            for name in common:
                if (a / name).is_file() and comparable_file(a / name):
                    logger.debug(
                        f"Submit initial file comparison: {common_path / name}"
                    )
                    Config.comparator.submit(common_path / name)
                elif (a / name).is_dir():
                    init_compare(a / name, b / name)

        logger.info("Kick off initial comparison of all files")
        init_compare(Config.path_a, Config.path_b)
        logger.info("Initial comparison submitted")

    def stop(self) -> None:
        logger.info("Stopping watchdog observer")
        self._observer.stop()

    def join(self) -> None:
        logger.info("Joining watchdog observer")
        self._observer.join()


class Comparator:
    def __init__(self, max_workers: int):
        def initializer():
            if Config.driver is not None:
                browser = getattr(Config.thread_local, "browser", None)
                if browser is None:
                    browser = get_browser(driver=Config.driver)
                    Config.thread_local.browser = browser

        logger.info(f"Creating comparator with {max_workers} workers")

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, initializer=initializer
        )
        self._result = {}
        self._future = {}

    def submit(self, path: Path) -> None:
        logger.debug(f"Submitting comparison for path: {path}")

        if not isinstance(path, Path):
            raise TypeError("Path must be of type Path")

        if path.suffix.lower() in [".html", ".htm"] and Config.driver is None:
            logger.debug(f"Skipping submission of HTML file without browser: {path}")
            return

        if path in self._future:
            try:
                self._future[path].cancel()
                self._future[path].result()
                self._future.pop(path)
            except Exception:
                pass

        self._result[path] = "pending"
        self._future[path] = self._executor.submit(self.compare, path)

    def compare(self, path: Path) -> None:
        logger.debug(f"Comparing files for path: {path}")

        if not isinstance(path, Path):
            raise TypeError("Path must be of type Path")
        if path not in self._future:
            raise RuntimeError("Path not submitted for comparison")

        browser = getattr(Config.thread_local, "browser", None)
        result = compare_files(
            Config.path_a / path,
            Config.path_b / path,
            browser=browser,
        )
        self._result[path] = "same" if result else "different"
        self._future.pop(path)

    def result(self, path: Path) -> str | None:
        logger.debug(f"Getting comparison result for path: {path}")

        if not isinstance(path, Path):
            raise TypeError("Path must be of type Path")

        if path in self._result:
            return self._result[path]

        if (Config.path_a / path).is_dir():
            a = Config.path_a / path
            b = Config.path_b / path

            left = sorted(p.name for p in a.iterdir())
            right = sorted(p.name for p in b.iterdir())

            left_relevant = sorted(
                [
                    name
                    for name in left
                    if (a / name).is_dir()
                    or ((a / name).is_file() and comparable_file(a / name))
                ]
            )
            right_relevant = sorted(
                [
                    name
                    for name in right
                    if (b / name).is_dir()
                    or ((b / name).is_file() and comparable_file(b / name))
                ]
            )

            common = [name for name in left_relevant if name in right_relevant]
            left_missing = [
                name for name in right_relevant if name not in left_relevant
            ]
            right_missing = [
                name for name in left_relevant if name not in right_relevant
            ]

            return functools.reduce(
                lambda a, b: (
                    None
                    if None in (a, b)
                    else (
                        "pending"
                        if "pending" in (a, b)
                        else "different" if "different" in (a, b) else "same"
                    )
                ),
                [self.result(path / name) for name in common]
                + [
                    (
                        "different"
                        if len(left_missing) + len(right_missing) > 0
                        else "same"
                    )
                ],
                "same",
            )

        logger.debug(f"No comparison result for path: {path}")
        return None

    def all_results(self) -> dict[str, str]:
        # Snapshot the per-file results as a plain {path: result} map.
        # Cheap O(n) dict copy used by the live-status endpoint; no tree walk.
        return {str(path): result for path, result in list(self._result.items())}


app = Flask("compare")


@app.route("/script.js")
def script_js():
    logger.debug("Serving script.js")

    return """
function updateRef(path) {
  fetch(`/update_ref/${path}`)
    .then(response => {
      if (response.ok) {
        alert(`Reference updated for ${path}`);
        location.reload();
      } else {
        alert(`Failed to update reference for ${path}: ${response.statusText}`);
      }
    })
    .catch(error => {
      alert(`Error updating reference for ${path}: ${error}`);
    });
}
"""


@app.route("/")
def root():
    logger.debug("Generating root directory listing")

    has_comparator = Config.comparator is not None

    def collect(a: Path, b: Path) -> list[dict]:
        """Single O(n) walk producing flat leaf rows.

        One row per comparable file (and per missing file/dir). File statuses
        are O(1) dict lookups, so there is no recursive per-directory status
        aggregation and the whole tree is walked exactly once.
        """
        entries = []

        common_path = a.relative_to(Config.path_a)

        left = sorted(p.name for p in a.iterdir())
        right = sorted(p.name for p in b.iterdir())

        left_files = {
            name
            for name in left
            if (a / name).is_file() and comparable_file(a / name)
        }
        right_files = {
            name
            for name in right
            if (b / name).is_file() and comparable_file(b / name)
        }
        left_dirs = {name for name in left if (a / name).is_dir()}
        right_dirs = {name for name in right if (b / name).is_dir()}

        for name in sorted(left_files | right_files):
            rel = common_path / name
            if name in left_files and name in right_files:
                result = Config.comparator.result(rel) if has_comparator else None
                entries.append(
                    {
                        "path": str(rel),
                        "comparable": True,
                        "message": "",
                        "result": result,
                    }
                )
            elif name in right_files:
                entries.append(
                    {
                        "path": str(rel),
                        "comparable": False,
                        "message": "missing in reference (A)",
                        "result": "different",
                    }
                )
            else:
                entries.append(
                    {
                        "path": str(rel),
                        "comparable": False,
                        "message": "missing in monitored (B)",
                        "result": "different",
                    }
                )

        for name in sorted(left_dirs ^ right_dirs):
            rel = common_path / name
            where = "reference (A)" if name in right_dirs else "monitored (B)"
            entries.append(
                {
                    "path": str(rel) + "/",
                    "comparable": False,
                    "message": f"directory missing in {where}",
                    "result": "different",
                }
            )

        for name in sorted(left_dirs & right_dirs):
            entries.extend(collect(a / name, b / name))

        return entries

    entries = collect(Config.path_a, Config.path_b)
    entries.sort(key=lambda e: e["path"])

    def badge(result: str | None) -> str:
        if result is None:
            return ""
        return f'<span class="badge {result}">{result_symbol(result) or ""} {result}</span>'

    rows = []
    for e in entries:
        path = e["path"]
        if e["comparable"]:
            name_cell = f'<a href="/compare/{path}" target="_blank">{path}</a>'
        else:
            name_cell = f"<span>{path}</span>"
        rows.append(
            f'<tr data-path="{path}" data-status="{e["result"] or ""}">'
            f'<td class="status">{badge(e["result"])}</td>'
            f'<td class="path">{name_cell}</td>'
            f'<td class="message">{e["message"]}</td>'
            f'<td class="actions"><button class="btn" onclick="updateRef(\'{path}\')">update ref</button></td>'
            f"</tr>"
        )

    log_link = ""
    if Config.log_file is not None:
        log_link = f'<a href="/logfile" target="_blank">log file</a>'

    head = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>compare-html</title>
<style>
:root {
  --green: #137333; --green-bg: #e6f4ea;
  --red: #c5221f;   --red-bg: #fce8e6;
  --amber: #b06000; --amber-bg: #fef7e0;
  --border: #e0e0e0; --muted: #5f6368;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: #202124;
  background: #fafafa;
}
header {
  position: sticky; top: 0; z-index: 2;
  background: #fff; border-bottom: 1px solid var(--border);
  padding: 12px 20px;
}
.title { font-size: 18px; font-weight: 600; }
.paths { font-size: 12px; color: var(--muted); margin-top: 4px; }
.paths code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.toolbar {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  margin-top: 10px;
}
.toolbar input[type="search"] {
  flex: 1 1 220px; min-width: 160px;
  padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px;
  font-size: 13px;
}
.chips { display: flex; gap: 6px; }
.chip {
  font-size: 12px; padding: 3px 9px; border-radius: 12px;
  background: #f1f3f4; color: var(--muted); white-space: nowrap;
}
.chip.same { background: var(--green-bg); color: var(--green); }
.chip.different { background: var(--red-bg); color: var(--red); }
.chip.pending { background: var(--amber-bg); color: var(--amber); }
.filters { display: flex; gap: 4px; }
.filters button {
  font-size: 12px; padding: 5px 10px; border: 1px solid var(--border);
  background: #fff; border-radius: 6px; cursor: pointer; color: var(--muted);
}
.filters button.active { background: #202124; color: #fff; border-color: #202124; }
.btn {
  font-size: 12px; padding: 4px 8px; border: 1px solid var(--border);
  background: #fff; border-radius: 6px; cursor: pointer;
}
.btn:hover { background: #f1f3f4; }
table { width: 100%; border-collapse: collapse; }
thead th {
  position: sticky; top: 0; z-index: 1;
  text-align: left; font-size: 12px; color: var(--muted);
  font-weight: 600; padding: 8px 12px; background: #f8f9fa;
  border-bottom: 1px solid var(--border);
}
tbody td { padding: 6px 12px; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
tbody tr:hover { background: #f8f9fa; }
td.path { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
td.path a { color: #1a73e8; text-decoration: none; }
td.path a:hover { text-decoration: underline; }
td.path span { color: var(--muted); }
td.message { color: var(--muted); font-size: 12px; }
td.status { white-space: nowrap; }
.badge {
  display: inline-block; padding: 1px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.badge.same { background: var(--green-bg); color: var(--green); }
.badge.different { background: var(--red-bg); color: var(--red); }
.badge.pending { background: var(--amber-bg); color: var(--amber); }
</style>
<script src="/script.js"></script>
</head>
<body>
"""

    header = f"""<header>
  <div class="title">compare-html</div>
  <div class="paths">
    <div>reference (A): <code>{Config.path_a}</code></div>
    <div>monitored (B): <code>{Config.path_b}</code></div>
  </div>
  <div class="toolbar">
    <input type="search" id="search" placeholder="filter by path…" oninput="applyFilter()">
    <div class="chips">
      <span class="chip" id="chip-total">0 total</span>
      <span class="chip same" id="chip-same">0 same</span>
      <span class="chip different" id="chip-different">0 diff</span>
      <span class="chip pending" id="chip-pending">0 pending</span>
    </div>
    <div class="filters">
      <button data-filter="all" class="active" onclick="setFilter(this)">All</button>
      <button data-filter="different" onclick="setFilter(this)">Differences</button>
      <button data-filter="pending" onclick="setFilter(this)">Pending</button>
      <button data-filter="same" onclick="setFilter(this)">Same</button>
    </div>
    {log_link}
  </div>
</header>
"""

    table = (
        "<table><thead><tr>"
        "<th>Status</th><th>Path</th><th>Message</th><th>Actions</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )

    script = (
        """
<script>
const LIVE = """
        + ("true" if has_comparator else "false")
        + """;
const SYM = {pending: "🔄", same: "✔", different: "❌"};
let activeFilter = "all";

function badgeHtml(status) {
  if (!status) return "";
  return `<span class="badge ${status}">${SYM[status] || ""} ${status}</span>`;
}

function setFilter(btn) {
  activeFilter = btn.dataset.filter;
  document.querySelectorAll(".filters button")
    .forEach(b => b.classList.toggle("active", b === btn));
  applyFilter();
}

function applyFilter() {
  const q = document.getElementById("search").value.toLowerCase();
  document.querySelectorAll("tbody tr").forEach(tr => {
    const st = tr.dataset.status;
    const matchesFilter = activeFilter === "all" || st === activeFilter;
    const matchesSearch = !q || tr.dataset.path.toLowerCase().includes(q);
    tr.hidden = !(matchesFilter && matchesSearch);
  });
}

function updateSummary() {
  const counts = {total: 0, same: 0, different: 0, pending: 0};
  document.querySelectorAll("tbody tr").forEach(tr => {
    counts.total++;
    const st = tr.dataset.status;
    if (counts[st] !== undefined) counts[st]++;
  });
  document.getElementById("chip-total").textContent = counts.total + " total";
  document.getElementById("chip-same").textContent = counts.same + " same";
  document.getElementById("chip-different").textContent = counts.different + " diff";
  document.getElementById("chip-pending").textContent = counts.pending + " pending";
}

async function poll() {
  try {
    const res = await fetch("/status");
    if (!res.ok) return;
    const data = await res.json();
    document.querySelectorAll("tbody tr").forEach(tr => {
      const st = data[tr.dataset.path];
      if (st && st !== tr.dataset.status) {
        tr.dataset.status = st;
        tr.querySelector(".status").innerHTML = badgeHtml(st);
      }
    });
    updateSummary();
    applyFilter();
  } catch (e) {}
}

updateSummary();
if (LIVE) setInterval(poll, 1500);
</script>
</body>
</html>
"""
    )

    return head + header + table + script


@app.route("/status")
def status():
    logger.debug("Serving comparison status")

    if Config.comparator is None:
        return {}

    return Config.comparator.all_results()


@app.route("/logfile")
def logfile():
    logger.debug("Serving log file")

    if Config.log_file is None:
        return "No log file configured", 404

    return send_from_directory(Config.log_file.parent, Config.log_file.name)


@app.route("/compare/<path:path>")
def compare(path: str):
    logger.debug(f"Generating comparison page for path: {path}")

    if not isinstance(path, str):
        raise TypeError("Path must be a string")

    return f"""<!DOCTYPE html>
<html>
<head>
<style>
html,body {{height:100%;margin:0;}}
</style>
<script src="/script.js"></script>
</head>
<body style="display:flex;flex-flow:row;">
<div style="display:flex;flex:1;flex-flow:column;margin:5px;">
  <a href="/file/a/{path}" target="_blank">{Config.path_a / path}</a>
  <iframe id="a" src="/file/a/{path}" title="a" frameborder="0" align="left" style="flex:1;"></iframe>
</div>
<div style="display:flex;flex:0 0 50px;flex-flow:column;">
  <a href="/image_diff/{path}" target="_blank">diff</a>
  <button onclick="updateRef('{path}')">▶</button>
  <img src="/image_diff/{path}" width="50" height="0" style="flex:1;">
</div>
<div style="display:flex;flex:1;flex-flow:column;margin:5px;">
  <a href="/file/b/{path}" target="_blank">{Config.path_b / path}</a>
  <iframe id="b" src="/file/b/{path}" title="b" frameborder="0" align="right" style="flex:1;"></iframe>
</div>
<script>
var iframe_a = document.getElementById('a');
var iframe_b = document.getElementById('b');
iframe_a.contentWindow.addEventListener('scroll', function(event) {{
  iframe_b.contentWindow.scrollTo(iframe_a.contentWindow.scrollX, iframe_a.contentWindow.scrollY);
}});
iframe_b.contentWindow.addEventListener('scroll', function(event) {{
  iframe_a.contentWindow.scrollTo(iframe_b.contentWindow.scrollX, iframe_b.contentWindow.scrollY);
}});
</script>
</body>
</html>
"""


@app.route("/image_diff/<path:path>")
def image_diff(path: str):
    logger.debug(f"Generating image diff for path: {path}")

    if not isinstance(path, str):
        raise TypeError("Path must be a string")

    if Config.driver is None:
        return "Image diff not available without browser driver", 404

    diff, _ = html_render_diff(
        Config.path_a / path,
        Config.path_b / path,
        Config.browser,
    )
    tmp = io.BytesIO()
    diff.save(tmp, "JPEG", quality=70)
    tmp.seek(0)
    return send_file(tmp, mimetype="image/jpeg")


@app.route("/file/<variant>/<path:path>")
def file(variant: str, path: str):
    logger.debug(f"Serving file for variant: {variant}, path: {path}")

    if not isinstance(variant, str) or not isinstance(path, str):
        raise TypeError("Variant and path must be strings")
    if variant not in ["a", "b"]:
        raise ValueError("Variant must be 'a' or 'b'")

    variant_root = Config.path_a if variant == "a" else Config.path_b
    return send_from_directory(variant_root, path)


@app.route("/update_ref/<path:path>")
def update_ref(path: str):
    logger.debug(f"Updating reference for path: {path}")

    if not isinstance(path, str):
        raise TypeError("Path must be a string")

    src = Config.path_b / path
    dst = Config.path_a / path

    if not src.exists():
        return f"Source file does not exist: {src}", 404

    if src.is_file():
        shutil.copy2(src, dst)
    else:
        shutil.copytree(src, dst, dirs_exist_ok=True)

    return "Reference updated", 200


def verbosity_to_level(verbosity: int) -> int:
    if verbosity >= 3:
        return logging.DEBUG
    elif verbosity == 2:
        return logging.INFO
    elif verbosity == 1:
        return logging.WARNING
    else:
        return logging.ERROR


def setup_logging(
    verbosity: int, log_file: Path = None, log_file_verbosity: int = None
) -> None:
    level = verbosity_to_level(verbosity)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_file is not None:
        file_level = (
            verbosity_to_level(log_file_verbosity)
            if log_file_verbosity is not None
            else level
        )

        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(file_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ref", type=Path, help="Path to the reference directory")
    parser.add_argument("mon", type=Path, help="Path to the monitored directory")
    parser.add_argument("--driver", choices=["chrome", "firefox"])
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v, -vv, -vvv)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Path to log file",
    )
    parser.add_argument(
        "--log-file-verbosity", type=int, help="Log file verbosity level"
    )
    args = parser.parse_args()

    setup_logging(args.verbose, args.log_file, args.log_file_verbosity)

    Config.path_a = args.ref
    Config.path_b = args.mon
    Config.driver = args.driver
    Config.browser = (
        get_browser(driver=args.driver) if args.driver is not None else None
    )
    Config.log_file = args.log_file

    if args.compare:
        Config.comparator = Comparator(max_workers=args.max_workers)

        Config.observer = Observer()
        Config.observer.start()

    app.run(host="0.0.0.0", port=args.port)

    if args.compare:
        Config.observer.stop()
        Config.observer.join()

    return 0


if __name__ == "__main__":
    sys.exit(main())
