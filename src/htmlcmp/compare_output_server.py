#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import sys
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


def result_symbol(result: str):
    if not isinstance(result, str):
        raise TypeError("Result must be of type str")

    if result == "pending":
        return "🔄"
    if result == "same":
        return "✔"
    if result == "different":
        return "❌"
    return "⛔"


def result_css(result: str):
    if not isinstance(result, str):
        raise TypeError("Result must be of type str")

    if result == "pending":
        return "color:blue;"
    if result == "same":
        return "color:green;"
    if result == "different":
        return "color:orange;"
    return "color:red;"


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

    def start(self):
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

    def stop(self):
        logger.info("Stopping watchdog observer")
        self._observer.stop()

    def join(self):
        logger.info("Joining watchdog observer")
        self._observer.join()


class Comparator:
    def __init__(self, max_workers: int):
        def initializer():
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

    def submit(self, path: Path):
        logger.debug(f"Submitting comparison for path: {path}")

        if not isinstance(path, Path):
            raise TypeError("Path must be of type Path")

        if path in self._future:
            try:
                self._future[path].cancel()
                self._future[path].result()
                self._future.pop(path)
            except Exception:
                pass

        self._result[path] = "pending"
        self._future[path] = self._executor.submit(self.compare, path)

    def compare(self, path: Path):
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

    def result(self, path: Path):
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
                    "pending"
                    if "pending" in (a, b)
                    else ("different" if "different" in (a, b) else "same")
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

        logger.warning(f"No comparison result for path: {path}")
        return "unknown"


app = Flask("compare")


@app.route("/")
def root():
    logger.debug("Generating root directory listing")

    current_entry_id = 0

    def next_entry_id():
        nonlocal current_entry_id
        entry_id = current_entry_id
        current_entry_id += 1
        return entry_id

    def generate_entry(
        entry_id: int,
        parrent_id: int | None,
        depth: int,
        is_directory: bool,
        is_comparable: bool,
        path: Path,
        name: str,
        message: str = "",
        cmp_result: str = None,
    ) -> str:
        result = ""

        if cmp_result is None and Config.comparator is not None:
            cmp_result = Config.comparator.result(path)
        is_hidden = (
            Config.comparator is not None
            and Config.comparator.result(path.parent) == "same"
        )
        is_collapsed = cmp_result == "same"

        hidden_str = "hidden" if is_hidden else ""
        result += f'<tr data-entry-id="{entry_id}" data-parent-id="{"" if parrent_id is None else parrent_id}" data-depth="{depth}" {hidden_str}>'

        if is_directory:
            result += f'<td><button class="toggle">{"▶" if is_collapsed else "▼"}</button></td>'
        else:
            result += "<td></td>"

        if is_comparable:
            main = f'<a href="/compare/{path}">{name}</a>'
        else:
            main = f"{name}"
        if cmp_result is not None:
            status = result_symbol(cmp_result)
            style = result_css(cmp_result)
            result += f'<td style="{style}">{status}</td>'
        else:
            style = ""
            result += f"<td></td>"
        result += f'<td class="main" style="{style}">{main}</td>'
        result += f"<td>{message}</td>"

        result += "</tr>"

        return result

    def generate_tree(
        a: Path, b: Path, name: str, parrent_id: int | None, depth: int
    ) -> str:
        result = ""

        if not isinstance(a, Path) or not isinstance(b, Path):
            raise TypeError("Paths must be of type Path")
        if not a.is_dir() or not b.is_dir():
            raise ValueError("Both paths must be directories")

        common_path = a.relative_to(Config.path_a)

        directory_id = next_entry_id()
        result += generate_entry(
            directory_id,
            parrent_id,
            depth,
            True,
            False,
            common_path,
            name + "/",
        )

        left = sorted(p.name for p in a.iterdir())
        right = sorted(p.name for p in b.iterdir())

        left_files = sorted(
            [
                name
                for name in left
                if (a / name).is_file() and comparable_file(a / name)
            ]
        )
        right_files = sorted(
            [
                name
                for name in right
                if (b / name).is_file() and comparable_file(b / name)
            ]
        )

        left_dirs = sorted([name for name in left if (a / name).is_dir()])
        right_dirs = sorted([name for name in right if (b / name).is_dir()])

        common_files = [name for name in left_files if name in right_files]
        common_dirs = [name for name in left_dirs if name in right_dirs]

        left_files_missing = [name for name in right_files if name not in left_files]
        right_files_missing = [name for name in left_files if name not in right_files]
        left_dirs_missing = [name for name in right_dirs if name not in left_dirs]
        right_dirs_missing = [name for name in left_dirs if name not in right_dirs]
        for name in left_files_missing:
            result += generate_entry(
                next_entry_id(),
                directory_id,
                depth + 1,
                False,
                False,
                common_path / name,
                name,
                "file missing in A",
                cmp_result="different",
            )
        for name in right_files_missing:
            result += generate_entry(
                next_entry_id(),
                directory_id,
                depth + 1,
                False,
                False,
                common_path / name,
                name,
                "file missing in B",
                cmp_result="different",
            )
        for name in left_dirs_missing:
            result += generate_entry(
                next_entry_id(),
                directory_id,
                depth + 1,
                False,
                False,
                common_path / name,
                name + "/",
                "dir missing in A",
                cmp_result="different",
            )
        for name in right_dirs_missing:
            result += generate_entry(
                next_entry_id(),
                directory_id,
                depth + 1,
                False,
                False,
                common_path / name,
                name + "/",
                "dir missing in B",
                cmp_result="different",
            )

        for name in common_files:
            result += generate_entry(
                next_entry_id(),
                directory_id,
                depth + 1,
                False,
                True,
                common_path / name,
                name,
                "",
            )

        for name in common_dirs:
            result += generate_tree(a / name, b / name, name, directory_id, depth + 1)

        return result

    result = """<!DOCTYPE html>
<html>
<head>
<style>
tr {
  --depth: attr(data-depth number);
}
.main {
  padding-left: calc(1.0rem * var(--depth));
}
</style>
</head>
<body>
"""

    result += "<p>"
    result += "comparing<br>"
    result += f"A: {Config.path_a}<br>"
    result += f"B: {Config.path_b}"
    result += "</p>"

    result += "<p>"
    result += '<button onclick="toggleAll(true)">Expand All</button>'
    result += '<button onclick="toggleAll(false)">Collapse All</button>'
    result += "</p>"

    result += "<table>"
    result += "<thead>"
    result += "<tr><td></td><td></td><td>Name</td><td>Message</td></tr>"
    result += "</thead>"
    result += "<tbody>"
    result += generate_tree(Config.path_a, Config.path_b, "", None, 0)
    result += "</tbody>"
    result += "</table>"

    result += """
<script>
document.addEventListener("click", e => {
  if (!e.target.classList.contains("toggle")) return;

  const row = e.target.closest("tr");
  const id = row.dataset.entryId;
  const expanded = e.target.textContent === "▼";

  e.target.textContent = expanded ? "▶" : "▼";

  toggleChildren(id, !expanded);
});

function toggleChildren(parentId, show) {
  document.querySelectorAll(`tr[data-parent-id="${parentId}"]`)
    .forEach(child => {
      child.hidden = !show;
    });
}

function toggleAll(show) {
  document.querySelectorAll("tr")
    .forEach(row => {
      const isRoot = !row.dataset.parentId;
      row.hidden = !isRoot && !show;
      const toggle = row.querySelector(".toggle");
      if (toggle) {
        toggle.textContent = show ? "▼" : "▶";
      }
    });
}
</script>
</body>
</html>
"""

    return result


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
</head>
<body style="display:flex;flex-flow:row;">
<div style="display:flex;flex:1;flex-flow:column;margin:5px;">
  <a href="/file/a/{path}">{Config.path_a / path}</a>
  <iframe id="a" src="/file/a/{path}" title="a" frameborder="0" align="left" style="flex:1;"></iframe>
</div>
<div style="display:flex;flex:0 0 50px;flex-flow:column;">
  <a href="/image_diff/{path}">diff</a>
  <img src="/image_diff/{path}" width="50" height="0" style="flex:1;">
</div>
<div style="display:flex;flex:1;flex-flow:column;margin:5px;">
  <a href="/file/b/{path}">{Config.path_b / path}</a>
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


def setup_logging(verbosity: int):
    if verbosity >= 3:
        level = logging.DEBUG
    elif verbosity == 2:
        level = logging.INFO
    elif verbosity == 1:
        level = logging.WARNING
    else:
        level = logging.ERROR

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("a", type=Path, help="Path to the first directory")
    parser.add_argument("b", type=Path, help="Path to the second directory")
    parser.add_argument(
        "--driver", choices=["chrome", "firefox", "phantomjs"], default="firefox"
    )
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
    args = parser.parse_args()

    setup_logging(args.verbose)

    Config.path_a = args.a
    Config.path_b = args.b
    Config.driver = args.driver
    Config.browser = get_browser(driver=args.driver)

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
