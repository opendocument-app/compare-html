"""Exercise deadlines with real subprocesses, without requiring a browser."""

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import psutil
import pytest

from htmlcmp import comparison_worker as worker_module
from htmlcmp.comparison_worker import ComparisonExecutor, ComparisonWorker


def controlled_worker(connection, driver, timeout):
    while True:
        request = connection.recv()
        if request is None:
            return
        a, b, kwargs = request
        if a.name == "hang.html":
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"]
            )
            b.write_text(f"{os.getpid()} {child.pid}")
            time.sleep(60)
        elif a.name == "error.html":
            connection.send((False, "ReadTimeoutError: screenshot timed out"))
            connection.recv()
        elif a.name == "crash.html":
            os._exit(1)
        else:
            connection.send((True, os.getpid()))


def hanging_shutdown(connection, driver, timeout):
    connection.recv()
    connection.send((True, os.getpid()))
    connection.recv()
    time.sleep(60)


def no_startup_response(connection, driver, timeout):
    time.sleep(60)


def test_timeout_kills_descendants_and_next_file_gets_a_new_worker(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(worker_module, "_compare_loop", controlled_worker)
    worker = ComparisonWorker("firefox", timeout=2)
    try:
        original = worker.compare(Path("healthy.html"), tmp_path / "unused")
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="browser discarded"):
            worker.compare(Path("hang.html"), tmp_path / "pids")
        assert time.monotonic() - started < 10
        parent, child = map(int, (tmp_path / "pids").read_text().split())
        assert original == parent
        assert not psutil.pid_exists(parent)
        try:
            assert psutil.Process(child).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass
        assert worker.compare(Path("healthy.html"), tmp_path / "unused") != original
    finally:
        worker.close()


def test_browser_error_does_not_poison_later_tasks(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_module, "_compare_loop", controlled_worker)
    with ComparisonExecutor(1, "firefox", timeout=5) as executor:
        first = executor.submit(
            executor.compare, Path("healthy.html"), tmp_path
        ).result()
        error = executor.submit(executor.compare, Path("error.html"), tmp_path)
        healthy = executor.submit(executor.compare, Path("healthy.html"), tmp_path)
        with pytest.raises(RuntimeError, match="ReadTimeoutError"):
            error.result()
        replacement = healthy.result()
        assert replacement != first
        assert (
            executor.submit(executor.compare, Path("healthy.html"), tmp_path).result()
            == replacement
        )
    assert not psutil.pid_exists(replacement)


def test_abrupt_worker_exit_does_not_break_the_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_module, "_compare_loop", controlled_worker)
    worker = ComparisonWorker("firefox", timeout=5)
    try:
        with pytest.raises((EOFError, ConnectionResetError)):
            worker.compare(Path("crash.html"), tmp_path)
        assert worker.compare(Path("healthy.html"), tmp_path)
    finally:
        worker.close()


def test_deadline_includes_browser_startup(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_module, "_compare_loop", no_startup_response)
    worker = ComparisonWorker("firefox", timeout=0.2)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        worker.compare(Path("healthy.html"), tmp_path)
    assert time.monotonic() - started < 5
    worker.close()


def test_shutdown_does_not_wait_forever_for_selenium_quit(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_module, "_compare_loop", hanging_shutdown)
    worker = ComparisonWorker("firefox", timeout=5)
    pid = worker.compare(Path("healthy.html"), tmp_path)
    started = time.monotonic()
    worker.close()
    assert time.monotonic() - started < 8
    assert not psutil.pid_exists(pid)
    worker.close()


def test_driver_none_never_starts_a_process(monkeypatch, tmp_path):
    start = Mock(side_effect=AssertionError("browser must not start"))
    monkeypatch.setattr(ComparisonWorker, "_start", start)
    a, b = tmp_path / "a.html", tmp_path / "b.html"
    a.write_text("one")
    b.write_text("two")
    with ComparisonExecutor(1, None) as executor:
        assert executor.submit(executor.compare, a, b).result() is False
    start.assert_not_called()


def test_identical_html_does_not_need_a_browser(tmp_path):
    a, b = tmp_path / "a.html", tmp_path / "b.html"
    a.write_text("identical")
    b.write_text("identical")
    # An invalid driver would fail if startup were attempted.
    with ComparisonExecutor(1, "not-a-browser", timeout=5) as executor:
        assert executor.submit(executor.compare, a, b).result() is True


def test_comparison_deadline_reaches_browser_transport(monkeypatch, tmp_path):
    a, b = tmp_path / "a.html", tmp_path / "b.html"
    a.write_text("one")
    b.write_text("two")
    connection = Mock()
    connection.recv.side_effect = [(a, b, {}), None]
    factory = Mock()
    monkeypatch.setattr(worker_module, "get_browser", factory)
    monkeypatch.setattr(worker_module, "compare_files", Mock(return_value=True))
    worker_module._compare_loop(connection, "firefox", 900)
    factory.assert_called_once_with("firefox", timeout=900)
    connection.send.assert_called_once_with((True, True))


def test_startup_failure_is_reported_per_file(tmp_path):
    a, b = tmp_path / "a.html", tmp_path / "b.html"
    a.write_text("one")
    b.write_text("two")
    worker = ComparisonWorker("not-a-browser", timeout=5)
    try:
        with pytest.raises(RuntimeError, match="Unsupported driver"):
            worker.compare(a, b)
        a.write_text("two")
        assert worker.compare(a, b) is True
    finally:
        worker.close()


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_cli_rejects_invalid_deadline(monkeypatch, timeout):
    from htmlcmp.compare_output_cli import main

    monkeypatch.setattr(sys, "argv", ["compare-html", "a", "b", "--timeout", timeout])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
