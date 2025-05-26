#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import argparse
import threading
import io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, send_from_directory, send_file
import watchdog.observers
import watchdog.events

from compare_output import comparable_file, compare_files
from html_render_diff import get_browser, html_render_diff


class Config:
    path_a: Path = None
    path_b: Path = None
    driver: str = None
    observer = None
    comparator = None
    browser = None
    thread_local = threading.local()


class Observer:
    def __init__(self):
        class Handler(watchdog.events.FileSystemEventHandler):
            def __init__(self, path):
                self._path = path

            def dispatch(self, event):
                if event.event_type in ["opened"]:
                    return

                if event.src_path.is_file():
                    Config.comparator.submit(event.src_path.relative_to(self._path))

        self._observer = watchdog.observers.Observer()
        self._observer.schedule(Handler(Config.path_a), Config.path_a, recursive=True)
        self._observer.schedule(Handler(Config.path_b), Config.path_b, recursive=True)

    def start(self):
        self._observer.start()

        def init_compare(a: Path, b: Path):
            if not isinstance(a, Path) or not isinstance(b, Path):
                raise TypeError("Paths must be of type Path")
            if not a.is_dir() or not b.is_dir():
                raise ValueError("Both paths must be directories")

            common_path = a / Config.path_a

            left = sorted(p.name for p in a.iterdir())
            right = sorted(p.name for p in b.iterdir())

            common = [name for name in left if name in right]

            for name in common:
                if (a / name).is_file() and comparable_file(a / name):
                    Config.comparator.submit(common_path / name)
                elif (a / name).is_dir():
                    init_compare(a / name, b / name)

        init_compare(Config.path_a, Config.path_b)

    def stop(self):
        self._observer.stop()

    def join(self):
        self._observer.join()


class Comparator:
    def __init__(self, max_workers: int):
        def initializer():
            browser = getattr(Config.thread_local, "browser", None)
            if browser is None:
                browser = get_browser(driver=Config.driver)
                Config.thread_local.browser = browser

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, initializer=initializer
        )
        self._result = {}
        self._future = {}

    def submit(self, path: Path):
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
        if not isinstance(path, Path):
            raise TypeError("Path must be of type Path")
        if not path.is_file():
            raise ValueError("Path must be a file")
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
        if not isinstance(path, Path):
            raise TypeError("Path must be of type Path")

        if path in self._result:
            return self._result[path]
        return "unknown"

    def result_symbol(self, path: Path):
        if not isinstance(path, Path):
            raise TypeError("Path must be of type Path")

        result = self.result(path)
        if result == "pending":
            return "🔄"
        if result == "same":
            return "✔"
        if result == "different":
            return "❌"
        return "⛔"

    def result_css(self, path: Path):
        if not isinstance(path, Path):
            raise TypeError("Path must be of type Path")

        result = self.result(path)
        if result == "pending":
            return "color:blue;"
        if result == "same":
            return "color:green;"
        if result == "different":
            return "color:orange;"
        return "color:red;"


app = Flask("compare")


@app.route("/")
def root():
    def print_tree(a: Path, b: Path):
        if not isinstance(a, Path) or not isinstance(b, Path):
            raise TypeError("Paths must be of type Path")
        if not a.is_dir() or not b.is_dir():
            raise ValueError("Both paths must be directories")

        common_path = a / Config.path_a

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

        result = "<ul>"

        left_files_missing = " ".join(
            [name for name in right_files if name not in left_files]
        )
        right_files_missing = " ".join(
            [name for name in left_files if name not in right_files]
        )
        left_dirs_missing = " ".join(
            [name for name in right_dirs if name not in left_dirs]
        )
        right_dirs_missing = " ".join(
            [name for name in left_dirs if name not in right_dirs]
        )
        if left_files_missing:
            result += f"<li><b>A files missing: {left_files_missing}</b></li>"
        if right_files_missing:
            result += f"<li><b>B files missing: {right_files_missing}</b></li>"
        if left_dirs_missing:
            result += f"<li><b>A dirs missing: {left_dirs_missing}</b></li>"
        if right_dirs_missing:
            result += f"<li><b>B dirs missing: {right_dirs_missing}</b></li>"

        for name in common_files:
            if Config.comparator is None:
                result += f'<li><a href="/compare/{common_path / name}">{name}</a></li>'
            else:
                symbol = Config.comparator.result_symbol(common_path / name)
                css = Config.comparator.result_css(common_path / name)
                result += f'<li style="{css}"><a style="{css}" href="/compare/{common_path / name}">{name}</a> {symbol}</li>'

        for name in common_dirs:
            result += f"<li>{name}"
            result += print_tree(a / name, b / name)
            result += "</li>"

        result += "</ul>"
        return result

    result = ""
    result += f"<p>compare A {Config.path_a} vs B {Config.path_b}</p>"
    result += print_tree(Config.path_a, Config.path_b)
    return result


@app.route("/compare/<path:path>")
def compare(path: str):
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
    if not isinstance(variant, str) or not isinstance(path, str):
        raise TypeError("Variant and path must be strings")
    if variant not in ["a", "b"]:
        raise ValueError("Variant must be 'a' or 'b'")

    variant_root = Config.path_a if variant == "a" else Config.path_b
    return send_from_directory(variant_root, path)


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
    args = parser.parse_args()

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
