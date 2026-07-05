#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import logging
import argparse
import subprocess
from pathlib import Path
from functools import partial
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

from htmlcmp.common import setup_logging

logger = logging.getLogger(__name__)


class Task:
    """A single file to run through tidy / JSON validation."""

    def __init__(self, rel: Path, path: Path):
        self.rel = rel
        self.path = path


class Failure:
    """A file that produced warnings or errors, or errored while tidying.

    ``kind`` is one of "warning" or "error". ``detail`` holds the captured
    tidy / validator output, shown when details are requested.
    """

    def __init__(self, rel: Path, kind: str, reason: str, detail: str = ""):
        self.rel = rel
        self.kind = kind
        self.reason = reason
        self.detail = detail


def tidy_json(path: Path) -> tuple[int, str]:
    """Validate a JSON file. Returns (status, detail); status 0 ok, 2 error."""
    if not isinstance(path, Path):
        raise TypeError("path must be a Path object")
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not a file")

    try:
        with open(path, "r") as f:
            json.load(f)
        return 0, ""
    except ValueError as exc:
        return 2, f"invalid JSON: {exc}"


def tidy_html(path: Path, html_tidy_config: Path = None) -> tuple[int, str]:
    """Run ``tidy`` on an HTML file.

    Returns (status, detail); status 0 ok, 1 warning, 2 error, mirroring
    tidy's own exit codes (0 / 1 / >1).
    """
    if not isinstance(path, Path):
        raise TypeError("path must be a Path object")
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not a file")
    if html_tidy_config is not None and not isinstance(html_tidy_config, Path):
        raise TypeError("html_tidy_config must be a Path object or None")
    if html_tidy_config is not None and not html_tidy_config.is_file():
        raise FileNotFoundError(f"{html_tidy_config} is not a file")

    cmd = ["tidy"]
    if html_tidy_config:
        cmd.extend(["-config", str(html_tidy_config.resolve())])
    cmd.append(str(path))
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    detail = result.stdout or ""
    if result.returncode == 1:
        return 1, detail
    if result.returncode > 1:
        return 2, detail
    return 0, detail


def tidy_file(path: Path, html_tidy_config: Path = None) -> tuple[int, str]:
    if not isinstance(path, Path):
        raise TypeError("path must be a Path object")
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not a file")

    if path.suffix == ".json":
        return tidy_json(path)
    if path.suffix == ".html":
        return tidy_html(path, html_tidy_config=html_tidy_config)
    # Not a tidyable file; treated as a no-op success.
    return 0, ""


def tidyable_file(path: Path) -> bool:
    if not isinstance(path, Path):
        raise TypeError("path must be a Path object")
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not a file")

    return path.suffix in (".json", ".html")


def collect_tasks(path: Path, root: Path = None) -> list[Task]:
    """Walk the tree once and return every tidyable file as a Task."""
    if not isinstance(path, Path):
        raise TypeError("path must be a Path object")
    if not path.is_dir():
        raise NotADirectoryError(f"{path} is not a directory")

    if root is None:
        root = path

    tasks: list[Task] = []

    items = sorted(path.iterdir())
    for item in items:
        if item.is_file() and tidyable_file(item):
            tasks.append(Task(item.relative_to(root), item))
        elif item.is_dir():
            tasks.extend(collect_tasks(item, root=root))

    return tasks


def run_task(task: Task, html_tidy_config: Path | None) -> tuple[int, str]:
    logger.debug("Tidying %s", task.rel)
    return tidy_file(task.path, html_tidy_config=html_tidy_config)


def make_executor(max_workers: int) -> ThreadPoolExecutor:
    logger.info("Creating executor with %d worker(s)", max_workers)
    return ThreadPoolExecutor(max_workers=max_workers)


def github_annotation(failure: Failure) -> None:
    """Emit a GitHub Actions annotation so warnings/errors surface in the UI."""
    # https://docs.github.com/actions/using-workflows/workflow-commands-for-github-actions
    level = "error" if failure.kind == "error" else "warning"
    print(f"::{level} file={failure.rel}::{failure.reason}")


def _style(kind: str) -> str:
    return "red" if kind == "error" else "yellow"


def _print_failure(console: Console, failure: Failure, show_details: bool) -> None:
    color = _style(failure.kind)
    mark = "✘" if failure.kind == "error" else "▲"
    console.print(
        f"[{color}]{mark}[/{color}] {escape(str(failure.rel))} "
        f"[{color}]— {escape(failure.reason)}[/{color}]"
    )
    if show_details and failure.detail:
        console.print(f"[dim]{escape(failure.detail.rstrip())}[/dim]")


def _resolve(task: Task, future, failures: list[Failure], github: bool):
    """Resolve a finished future into an optional Failure. Returns it or None."""
    try:
        status, detail = future.result()
    except Exception as exc:  # noqa: BLE001 - surface any tidy error as a failure
        logger.exception("Error tidying %s", task.rel)
        failure = Failure(task.rel, "error", f"error: {exc}")
    else:
        if status == 0:
            logger.debug("Clean: %s", task.rel)
            return None
        if status == 1:
            logger.info("Warnings: %s", task.rel)
            failure = Failure(task.rel, "warning", "has warnings", detail)
        else:
            logger.info("Errors: %s", task.rel)
            failure = Failure(task.rel, "error", "has errors", detail)

    failures.append(failure)
    if github:
        github_annotation(failure)
    return failure


def _run_live(future_to_task, console, failures, github, show_details):
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
        bar = progress.add_task("tidying…", total=len(future_to_task))
        for future in as_completed(future_to_task):
            task = future_to_task[future]
            # Transient line shows what's flowing through; only failures persist.
            progress.update(bar, description=str(task.rel))
            failure = _resolve(task, future, failures, github)
            if failure is not None:
                _print_failure(progress.console, failure, show_details)
            progress.advance(bar)


def _run_plain(future_to_task, console, failures, github, show_details):
    # No live region in CI / non-TTY: print failures as they happen plus a
    # periodic heartbeat so long runs still show they're alive.
    total = len(future_to_task)
    step = max(1, total // 20)
    done = 0
    for future in as_completed(future_to_task):
        task = future_to_task[future]
        failure = _resolve(task, future, failures, github)
        if failure is not None:
            _print_failure(console, failure, show_details)
        done += 1
        if done % step == 0 or done == total:
            console.print(f"[dim]  … {done}/{total} tidied[/dim]")


def _print_summary(console, total: int, failures: list[Failure]) -> None:
    console.rule("[bold]Summary")

    n_warning = sum(1 for f in failures if f.kind == "warning")
    n_error = sum(1 for f in failures if f.kind == "error")
    clean = total - n_warning - n_error

    if not failures:
        console.print(f"[green]✓ All {total} file(s) clean.[/green]")
        return

    parts = [f"[green]{clean} clean[/green]"]
    if n_warning:
        parts.append(f"[yellow]{n_warning} with warnings[/yellow]")
    if n_error:
        parts.append(f"[red]{n_error} with errors[/red]")
    console.print(", ".join(parts))

    console.print("\n[bold]Findings:[/bold]")
    for f in sorted(failures, key=lambda f: (f.kind, str(f.rel))):
        color = _style(f.kind)
        console.print(
            f"  [{color}]{escape(str(f.rel))}[/{color}] [dim]— {escape(f.reason)}[/dim]"
        )


def run(
    path: Path,
    *,
    html_tidy_config: Path | None,
    max_workers: int,
    console: Console,
    live: bool,
    github: bool,
    show_details: bool,
) -> int:
    console.print(f"[bold]Tidying[/bold] {escape(str(path))}")

    tasks = collect_tasks(path)
    logger.info("Collected %d tidyable file(s)", len(tasks))

    total = len(tasks)
    if total == 0:
        console.print("[dim]No tidyable files found.[/dim]")
        _print_summary(console, total, [])
        return 0

    failures: list[Failure] = []
    executor = make_executor(max_workers)
    try:
        future_to_task = {
            executor.submit(run_task, t, html_tidy_config): t for t in tasks
        }
        if live:
            _run_live(future_to_task, console, failures, github, show_details)
        else:
            _run_plain(future_to_task, console, failures, github, show_details)
    finally:
        executor.shutdown(wait=True)

    _print_summary(console, total, failures)

    # Warnings do not fail the run; only errors do.
    return 1 if any(f.kind == "error" for f in failures) else 0


def main():
    parser = argparse.ArgumentParser(
        prog="html-tidy",
        description="Run HTML tidy / JSON validation over a directory tree.",
    )
    parser.add_argument("path", type=Path, help="Path to directory to tidy")
    parser.add_argument(
        "--html-tidy-config", type=Path, help="Path to tidy config file"
    )
    parser.add_argument(
        "-j",
        "--max-workers",
        type=int,
        default=1,
        help="Number of parallel tidy workers (default: 1)",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Print the full tidy / validator output for each finding",
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

    if not args.path.is_dir():
        print(f"Argument must be a directory: {args.path}", file=sys.stderr)
        return 2

    console = Console()
    github = os.environ.get("GITHUB_ACTIONS") == "true"
    in_ci = bool(os.environ.get("CI"))
    live = console.is_terminal and not in_ci and not args.no_progress
    show_details = args.details or args.verbose > 0

    return run(
        args.path,
        html_tidy_config=args.html_tidy_config,
        max_workers=args.max_workers,
        console=console,
        live=live,
        github=github,
        show_details=show_details,
    )


if __name__ == "__main__":
    sys.exit(main())
