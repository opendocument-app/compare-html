#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import sys
import time
import shutil
import argparse
import logging
import threading
import functools
import collections
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, Response, render_template, send_from_directory, url_for
import watchdog.observers
import watchdog.events

from PIL import Image

from htmlcmp.common import (
    comparable_file,
    compare_files,
    setup_logging,
)
from htmlcmp.html_render_diff import get_browser, html_render_diff

logger = logging.getLogger(__name__)


class Config:
    path_a: Path = None
    path_b: Path = None
    driver: str = None
    observer = None
    comparator = None
    browser = None
    # Serializes access to the single shared ``browser`` above: Flask serves
    # requests from a thread pool and Selenium drivers are not thread-safe, so
    # concurrent /image_diff requests would otherwise interleave on one driver.
    browser_lock = threading.Lock()
    thread_local = threading.local()
    log_file: Path = None


class Observer:
    def __init__(self):
        class Handler(watchdog.events.FileSystemEventHandler):
            def __init__(self, path):
                self._path = path

            def dispatch(self, event):
                event_type = event.event_type

                logger.debug(f"Watchdog event: {event_type} {event.src_path}")

                if event_type not in ["moved", "deleted", "created", "modified"]:
                    return

                # A move surfaces both the old (src) and new (dest) locations;
                # everything else only touches src.
                paths = [Path(event.src_path)]
                if event_type == "moved" and getattr(event, "dest_path", None):
                    paths.append(Path(event.dest_path))

                for path in paths:
                    try:
                        rel = path.relative_to(self._path)
                    except ValueError:
                        continue

                    if path.is_file():
                        logger.debug(f"Submit watchdog file change: {path}")
                        Config.comparator.submit(rel)
                    else:
                        # Deleted/moved-away file: drop its stale cached result.
                        logger.debug(f"Forget watchdog file removal: {path}")
                        Config.comparator.forget(rel)

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

        browser = getattr(Config.thread_local, "browser", None)
        try:
            result = compare_files(
                Config.path_a / path,
                Config.path_b / path,
                browser=browser,
            )
        except Exception:
            # A file may have vanished or failed to render between submission
            # and comparison; report it as different rather than leaving the
            # path stuck on "pending" forever.
            logger.exception(f"Comparison failed for path: {path}")
            result = False
        self._result[path] = "same" if result else "different"
        # The worker can start before ``submit`` stored the future, so the
        # entry is not guaranteed to be there yet.
        self._future.pop(path, None)

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

    def forget(self, path: Path) -> None:
        # Drop any cached result/future for a path that no longer exists, so
        # deletions don't linger as stale state.
        logger.debug(f"Forgetting comparison for path: {path}")

        if not isinstance(path, Path):
            raise TypeError("Path must be of type Path")

        future = self._future.pop(path, None)
        if future is not None:
            future.cancel()
        self._result.pop(path, None)


class Accepted:
    """Remembers which files had their reference updated during this session.

    That is what "accepted" means on the pages: someone looked at the diff and
    promoted the monitored file to be the new reference. The mark is dropped
    again as soon as the monitored file changes after the update, so it never
    claims more than it knows. State is in-memory only.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._marks: dict[str, float] = {}

    def mark(self, path: str) -> None:
        logger.debug(f"Marking as accepted: {path}")

        with self._lock:
            self._marks[path] = time.time()

    def check(self, path: str) -> bool:
        with self._lock:
            timestamp = self._marks.get(path)

        if timestamp is None:
            return False

        try:
            stale = (Config.path_b / path).stat().st_mtime > timestamp
        except OSError:
            stale = True

        if stale:
            logger.debug(f"Dropping stale acceptance: {path}")
            with self._lock:
                self._marks.pop(path, None)
            return False

        return True


def highlight(diff: Image.Image) -> Image.Image:
    """Turn a raw difference image into something readable at a glance.

    Pixel-wise differences are mostly very dark, which is unusable as an image,
    so every differing pixel is painted in full-strength red on a near-black
    background.
    """
    mask = diff.convert("L").point(lambda value: 255 if value > 0 else 0)
    visual = Image.new("RGB", diff.size, (12, 14, 18))
    visual.paste((255, 76, 76), mask=mask)
    return visual


def diff_regions(diff: Image.Image, bands: int = 256) -> list[list[float]]:
    """Locate the vertical bands of the page that contain differences.

    Returned as [start, end] fractions of the page height. The compare page
    draws these as overlay bars: a few differing pixels vanish when the diff
    image itself is scaled into a narrow strip, whereas a band always stays
    visible. Costs one scan over the image.
    """
    width, height = diff.size
    band = max(1, height // bands)

    regions = []
    for top in range(0, height, band):
        bottom = min(height, top + band)
        if diff.crop((0, top, width, bottom)).getbbox() is None:
            continue
        if regions and regions[-1][1] == top:
            regions[-1][1] = bottom
        else:
            regions.append([top, bottom])

    return [[top / height, bottom / height] for top, bottom in regions]


class DiffCache:
    """Caches rendered diff images, keyed by the mtimes of both inputs.

    Rendering drives a real browser and is by far the most expensive thing the
    server does. The compare page wants both the image and its statistics, and
    asks again on every reload, so without a cache a single review step would
    pay for several full-page renders.
    """

    MAX_ENTRIES = 32

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = collections.OrderedDict()

    def get(self, path: str) -> tuple[bytes, dict]:
        a = Config.path_a / path
        b = Config.path_b / path
        key = (path, a.stat().st_mtime_ns, b.stat().st_mtime_ns)

        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                self._entries.move_to_end(key)
                logger.debug(f"Diff cache hit: {path}")
                return hit

        logger.debug(f"Rendering diff: {path}")
        with Config.browser_lock:
            diff, _ = html_render_diff(a, b, Config.browser)

        width, height = diff.size
        bbox = diff.getbbox()
        stats = {
            "available": True,
            "identical": bbox is None,
            "width": width,
            "height": height,
        }
        if bbox is not None:
            left, top, right, bottom = bbox
            stats["first_diff"] = top / height
            stats["area"] = ((right - left) * (bottom - top)) / (width * height)
            stats["regions"] = diff_regions(diff)

        buffer = io.BytesIO()
        highlight(diff).save(buffer, "PNG", optimize=True)
        result = (buffer.getvalue(), stats)

        with self._lock:
            self._entries[key] = result
            while len(self._entries) > self.MAX_ENTRIES:
                self._entries.popitem(last=False)

        return result


app = Flask(__name__)

accepted = Accepted()
diff_cache = DiffCache()


@app.context_processor
def template_helpers() -> dict:
    def static_url(filename: str) -> str:
        """URL for a static asset, tagged with its modification time.

        Keeps a page and its scripts in lockstep: a browser holding on to an
        older stylesheet or script after an upgrade would otherwise render a
        page whose markup no longer matches.
        """
        try:
            stamp = int((Path(app.static_folder) / filename).stat().st_mtime)
        except OSError:
            stamp = 0
        return url_for("static", filename=filename, v=stamp)

    return {"static_url": static_url}


@app.after_request
def no_store_html(response: Response) -> Response:
    # The listings are generated per request and go stale immediately.
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store"
    return response


def resolve_in(root: Path, path: str) -> Path | None:
    """Resolve ``path`` below ``root``, or None if it would escape it."""
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root.resolve()):
        logger.warning(f"Rejecting path outside of {root}: {path}")
        return None
    return candidate


def collect_entries() -> list[dict]:
    """Walk both trees once and produce flat per-leaf rows.

    Shared by the page render and the live-status endpoint so a poll reflects
    exactly the same state as a fresh reload, including files that were created
    or deleted after startup. File statuses are O(1) dict lookups, so the whole
    tree is walked exactly once with no recursive per-directory aggregation.
    """
    has_comparator = Config.comparator is not None

    def entry(path: Path, message: str, result: str | None) -> dict:
        key = str(path)
        return {
            "path": key,
            "message": message,
            "result": result,
            "accepted": accepted.check(key),
        }

    def collect_one_sided(existing: Path, root: Path, message: str) -> list[dict]:
        """Walk a directory present on only one side.

        Emits one row per comparable file inside it so each file stays viewable
        (on the present side) and copyable to the reference, just like a missing
        file. Recurses into nested directories.
        """
        entries = []

        for child in sorted(existing.iterdir(), key=lambda p: p.name):
            if child.is_dir():
                entries.extend(collect_one_sided(child, root, message))
            elif child.is_file() and comparable_file(child):
                entries.append(entry(child.relative_to(root), message, "different"))

        return entries

    def collect(a: Path, b: Path) -> list[dict]:
        entries = []

        common_path = a.relative_to(Config.path_a)

        left = sorted(p.name for p in a.iterdir())
        right = sorted(p.name for p in b.iterdir())

        left_files = {
            name for name in left if (a / name).is_file() and comparable_file(a / name)
        }
        right_files = {
            name for name in right if (b / name).is_file() and comparable_file(b / name)
        }
        left_dirs = {name for name in left if (a / name).is_dir()}
        right_dirs = {name for name in right if (b / name).is_dir()}

        for name in sorted(left_files | right_files):
            rel = common_path / name
            if name in left_files and name in right_files:
                result = Config.comparator.result(rel) if has_comparator else None
                entries.append(entry(rel, "", result))
            elif name in right_files:
                entries.append(entry(rel, "missing in reference (A)", "different"))
            else:
                entries.append(entry(rel, "missing in monitored (B)", "different"))

        for name in sorted(left_dirs ^ right_dirs):
            if name in left_dirs:
                entries.extend(
                    collect_one_sided(
                        a / name, Config.path_a, "missing in monitored (B)"
                    )
                )
            else:
                entries.extend(
                    collect_one_sided(
                        b / name, Config.path_b, "missing in reference (A)"
                    )
                )

        for name in sorted(left_dirs & right_dirs):
            entries.extend(collect(a / name, b / name))

        return entries

    entries = collect(Config.path_a, Config.path_b)
    entries.sort(key=lambda e: e["path"])
    return entries


@app.route("/")
def root():
    logger.debug("Generating root directory listing")

    return render_template(
        "index.html",
        entries=collect_entries(),
        live=Config.comparator is not None,
        path_a=Config.path_a,
        path_b=Config.path_b,
        log_file=Config.log_file,
    )


@app.route("/compare/<path:path>")
def compare(path: str):
    logger.debug(f"Generating comparison page for path: {path}")

    a = resolve_in(Config.path_a, path)
    b = resolve_in(Config.path_b, path)
    if a is None or b is None:
        return "Invalid path", 400

    return render_template(
        "compare.html",
        path=path,
        file_a=Config.path_a / path,
        file_b=Config.path_b / path,
        live=Config.comparator is not None,
        diff_available=Config.driver is not None and a.is_file() and b.is_file(),
    )


@app.route("/api/entries")
def api_entries():
    logger.debug("Serving entries")

    return {
        "live": Config.comparator is not None,
        "entries": collect_entries(),
    }


@app.route("/status")
def status():
    """Legacy status endpoint: a flat path -> result mapping."""
    logger.debug("Serving comparison status")

    if Config.comparator is None:
        return {}

    return {e["path"]: e["result"] for e in collect_entries()}


@app.route("/logfile")
def logfile():
    logger.debug("Serving log file")

    if Config.log_file is None:
        return "No log file configured", 404

    return send_from_directory(Config.log_file.parent, Config.log_file.name)


def render_diff(path: str) -> tuple[bytes | None, dict, int]:
    """Render (or fetch from cache) the diff for ``path``.

    Returns the PNG bytes, the statistics and an HTTP status code; the bytes
    are None when no diff could be produced.
    """
    if Config.driver is None:
        return None, {"available": False, "error": "no browser driver"}, 404

    a = resolve_in(Config.path_a, path)
    b = resolve_in(Config.path_b, path)
    if a is None or b is None:
        return None, {"available": False, "error": "invalid path"}, 400
    if not a.is_file() or not b.is_file():
        return None, {"available": False, "error": "file missing on one side"}, 404

    try:
        image, stats = diff_cache.get(path)
    except Exception as error:
        logger.exception(f"Failed to render diff for path: {path}")
        return None, {"available": False, "error": str(error)}, 500

    return image, stats, 200


@app.route("/image_diff/<path:path>")
def image_diff(path: str):
    logger.debug(f"Serving image diff for path: {path}")

    image, stats, code = render_diff(path)
    if image is None:
        return stats.get("error", "image diff not available"), code

    return Response(image, mimetype="image/png")


@app.route("/api/diff_info/<path:path>")
def api_diff_info(path: str):
    logger.debug(f"Serving diff info for path: {path}")

    _, stats, code = render_diff(path)
    return stats, code


@app.route("/file/<variant>/<path:path>")
def file(variant: str, path: str):
    logger.debug(f"Serving file for variant: {variant}, path: {path}")

    if variant not in ["a", "b"]:
        return "Variant must be 'a' or 'b'", 404

    variant_root = Config.path_a if variant == "a" else Config.path_b

    resolved = resolve_in(variant_root, path)
    if resolved is None:
        return "Invalid path", 400

    if not resolved.is_file():
        side = "reference (A)" if variant == "a" else "monitored (B)"
        return render_template("missing.html", side=side), 404

    return send_from_directory(variant_root, path)


def do_update_ref(path: str):
    src = resolve_in(Config.path_b, path)
    dst = resolve_in(Config.path_a, path)

    if src is None or dst is None:
        return {"error": "invalid path"}, 400
    if not src.exists():
        return {"error": f"source does not exist: {src}"}, 404

    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.is_file():
        shutil.copy2(src, dst)
    else:
        shutil.copytree(src, dst, dirs_exist_ok=True)

    accepted.mark(path)

    # The watchdog picks the copy up as well, but re-comparing right away keeps
    # the UI from briefly showing the old result after an accepted diff.
    if Config.comparator is not None and src.is_file():
        Config.comparator.submit(Path(path))

    return {"ok": True, "path": path}, 200


@app.route("/api/update_ref/<path:path>", methods=["POST"])
def api_update_ref(path: str):
    logger.debug(f"Updating reference for path: {path}")

    return do_update_ref(path)


@app.route("/update_ref/<path:path>", methods=["GET", "POST"])
def update_ref(path: str):
    """Legacy endpoint kept for scripted use; prefer POST /api/update_ref."""
    logger.debug(f"Updating reference for path: {path}")

    body, code = do_update_ref(path)
    if code != 200:
        return body["error"], code
    return "Reference updated", 200


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ref", type=Path, help="Path to the reference directory")
    parser.add_argument("mon", type=Path, help="Path to the monitored directory")
    parser.add_argument("--driver", choices=["chrome", "firefox"])
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host/interface to bind the server to (default: 0.0.0.0)",
    )
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

    app.run(host=args.host, port=args.port)

    if args.compare:
        Config.observer.stop()
        Config.observer.join()

    return 0


if __name__ == "__main__":
    sys.exit(main())
