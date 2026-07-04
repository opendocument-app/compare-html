#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import argparse
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.markup import escape
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from htmlcmp.html_render_diff import get_browser
from htmlcmp.common import comparable_file, compare_files, setup_logging

logger = logging.getLogger(__name__)


class Config:
    thread_local = threading.local()


class Task:
    """A single file comparison between the reference (A) and monitored (B) tree."""

    def __init__(self, rel: Path, a: Path, b: Path, diff_output: Path = None):
        self.rel = rel
        self.a = a
        self.b = b
        self.diff_output = diff_output


class Failure:
    """A file that differs, is missing on one side, or errored while comparing.

    ``kind`` is one of "different", "missing", or "error".
    """

    def __init__(self, rel: Path, kind: str, reason: str):
        self.rel = rel
        self.kind = kind
        self.reason = reason


def collect_tasks(
    a: Path, b: Path, root: Path = None, diff_output: Path = None
) -> tuple[list[Task], list[Failure]]:
    """Walk both trees once and return (comparable tasks, structural failures).

    Structural failures are files/directories present on only one side; they are
    known to differ up-front and never get submitted to the executor.
    """
    if not isinstance(a, Path) or not isinstance(b, Path):
        raise TypeError("Both arguments must be of type Path")
    if not a.is_dir() or not b.is_dir():
        raise FileNotFoundError("Both arguments must be directories")

    if root is None:
        root = a

    tasks: list[Task] = []
    failures: list[Failure] = []

    left = sorted(p.name for p in a.iterdir())
    right = sorted(p.name for p in b.iterdir())

    left_files = {
        name for name in left if (a / name).is_file() and comparable_file(a / name)
    }
    right_files = {
        name for name in right if (b / name).is_file() and comparable_file(b / name)
    }

    for name in sorted(left_files | right_files):
        rel = (a / name).relative_to(root)
        if name in left_files and name in right_files:
            tasks.append(
                Task(
                    rel,
                    a / name,
                    b / name,
                    None if diff_output is None else diff_output / name,
                )
            )
        elif name in left_files:
            failures.append(Failure(rel, "missing", "missing in monitored (B)"))
        else:
            failures.append(Failure(rel, "missing", "missing in reference (A)"))

    left_dirs = {name for name in left if (a / name).is_dir()}
    right_dirs = {name for name in right if (b / name).is_dir()}

    for name in sorted(left_dirs & right_dirs):
        sub_tasks, sub_failures = collect_tasks(
            a / name,
            b / name,
            root=root,
            diff_output=None if diff_output is None else diff_output / name,
        )
        tasks.extend(sub_tasks)
        failures.extend(sub_failures)

    for name in sorted(left_dirs - right_dirs):
        failures.append(
            Failure((a / name).relative_to(root), "missing", "missing in monitored (B)")
        )
    for name in sorted(right_dirs - left_dirs):
        failures.append(
            Failure((b / name).relative_to(root), "missing", "missing in reference (A)")
        )

    return tasks, failures


def run_task(task: Task) -> bool:
    logger.debug("Comparing %s", task.rel)
    browser = getattr(Config.thread_local, "browser", None)
    return compare_files(task.a, task.b, browser=browser, diff_output=task.diff_output)


def make_executor(max_workers: int, driver: str | None) -> ThreadPoolExecutor:
    def initializer():
        # One browser per worker thread. Without a driver we only ever compare
        # JSON / byte-identical files, so no browser is needed.
        if driver is not None:
            if getattr(Config.thread_local, "browser", None) is None:
                logger.info("Starting %s browser for worker thread", driver)
                Config.thread_local.browser = get_browser(driver=driver)

    logger.info("Creating executor with %d worker(s)", max_workers)
    return ThreadPoolExecutor(max_workers=max_workers, initializer=initializer)


def github_error(rel: Path, reason: str) -> None:
    """Emit a GitHub Actions error annotation so failures surface in the UI."""
    # https://docs.github.com/actions/using-workflows/workflow-commands-for-github-actions
    print(f"::error file={rel}::{reason}")


def _resolve(task: Task, future, failures: list[Failure], github: bool):
    """Resolve a finished future into an optional Failure. Returns it or None."""
    try:
        same = future.result()
    except Exception as exc:  # noqa: BLE001 - surface any comparison error as a failure
        logger.exception("Error comparing %s", task.rel)
        failure = Failure(task.rel, "error", f"error: {exc}")
    else:
        if same:
            logger.debug("Match: %s", task.rel)
            return None
        logger.info("Difference: %s", task.rel)
        failure = Failure(task.rel, "different", "different")

    failures.append(failure)
    if github:
        github_error(failure.rel, failure.reason)
    return failure


def _run_live(future_to_task, console, failures, github):
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    )
    with progress:
        bar = progress.add_task("comparing…", total=len(future_to_task))
        for future in as_completed(future_to_task):
            task = future_to_task[future]
            # Transient line shows what's flowing through; only failures persist.
            progress.update(bar, description=str(task.rel))
            failure = _resolve(task, future, failures, github)
            if failure is not None:
                progress.console.print(
                    f"[red]✘[/red] {escape(str(failure.rel))} "
                    f"[red]— {escape(failure.reason)}[/red]"
                )
            progress.advance(bar)


def _run_plain(future_to_task, console, failures, github):
    # No live region in CI / non-TTY: print failures as they happen plus a
    # periodic heartbeat so long runs still show they're alive.
    total = len(future_to_task)
    step = max(1, total // 20)
    done = 0
    for future in as_completed(future_to_task):
        task = future_to_task[future]
        failure = _resolve(task, future, failures, github)
        if failure is not None:
            console.print(
                f"[red]✘[/red] {escape(str(failure.rel))} "
                f"[red]— {escape(failure.reason)}[/red]"
            )
        done += 1
        if done % step == 0 or done == total:
            console.print(f"[dim]  … {done}/{total} compared[/dim]")


def _print_summary(console, total: int, failures: list[Failure]) -> None:
    console.rule("[bold]Summary")

    n_diff = sum(1 for f in failures if f.kind == "different")
    n_error = sum(1 for f in failures if f.kind == "error")
    n_missing = sum(1 for f in failures if f.kind == "missing")
    matched = total - n_diff - n_error

    if not failures:
        console.print(f"[green]✓ All {total} file(s) match.[/green]")
        return

    parts = [f"[green]{matched} matched[/green]"]
    if n_diff:
        parts.append(f"[red]{n_diff} different[/red]")
    if n_missing:
        parts.append(f"[red]{n_missing} missing[/red]")
    if n_error:
        parts.append(f"[red]{n_error} error[/red]")
    console.print(", ".join(parts))

    console.print("\n[red bold]Failures:[/red bold]")
    for f in sorted(failures, key=lambda f: (f.kind, str(f.rel))):
        console.print(
            f"  [red]{escape(str(f.rel))}[/red] [dim]— {escape(f.reason)}[/dim]"
        )


def run(
    a: Path,
    b: Path,
    *,
    driver: str | None,
    max_workers: int,
    diff_output: Path | None,
    console: Console,
    live: bool,
    github: bool,
) -> int:
    console.print(f"[bold]Comparing[/bold] {escape(str(a))} [dim]→[/dim] {escape(str(b))}")

    tasks, failures = collect_tasks(a, b, diff_output=diff_output)
    logger.info("Collected %d comparable file(s), %d structural difference(s)",
                len(tasks), len(failures))

    # Report structural failures (missing files/dirs) up-front.
    for f in sorted(failures, key=lambda f: str(f.rel)):
        console.print(
            f"[red]✘[/red] {escape(str(f.rel))} [red]— {escape(f.reason)}[/red]"
        )
        if github:
            github_error(f.rel, f.reason)

    total = len(tasks)
    if total == 0:
        console.print("[dim]No comparable files to compare.[/dim]")
    else:
        executor = make_executor(max_workers, driver)
        try:
            future_to_task = {executor.submit(run_task, t): t for t in tasks}
            if live:
                _run_live(future_to_task, console, failures, github)
            else:
                _run_plain(future_to_task, console, failures, github)
        finally:
            executor.shutdown(wait=True)

    _print_summary(console, total, failures)

    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(
        prog="compare-html",
        description="Compare two directory trees of rendered HTML/JSON files.",
    )
    parser.add_argument("a", type=Path, help="Reference directory (A)")
    parser.add_argument("b", type=Path, help="Monitored directory (B)")
    parser.add_argument(
        "--driver",
        choices=["chrome", "firefox", "none"],
        default="firefox",
        help="Browser used to render HTML diffs; 'none' compares only "
        "JSON and byte-identical files (default: firefox)",
    )
    parser.add_argument(
        "--diff-output", type=Path, help="Directory to write diff images for mismatches"
    )
    parser.add_argument(
        "-j",
        "--max-workers",
        type=int,
        default=1,
        help="Number of parallel comparison workers (default: 1)",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the live progress bar (forced off when not a TTY / in CI)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v, -vv, -vvv)",
    )
    parser.add_argument("--log-file", type=Path, help="Path to log file")
    parser.add_argument(
        "--log-file-verbosity", type=int, help="Log file verbosity level"
    )
    args = parser.parse_args()

    setup_logging(args.verbose, args.log_file, args.log_file_verbosity)

    if not args.a.is_dir() or not args.b.is_dir():
        print(f"Both arguments must be directories: {args.a} {args.b}", file=sys.stderr)
        return 2

    driver = None if args.driver == "none" else args.driver

    console = Console()
    github = os.environ.get("GITHUB_ACTIONS") == "true"
    in_ci = bool(os.environ.get("CI"))
    live = console.is_terminal and not in_ci and not args.no_progress

    return run(
        args.a,
        args.b,
        driver=driver,
        max_workers=args.max_workers,
        diff_output=args.diff_output,
        console=console,
        live=live,
        github=github,
    )


if __name__ == "__main__":
    sys.exit(main())
