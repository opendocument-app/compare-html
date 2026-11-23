"""Reusable browser processes with a deadline owned by the caller."""

import filecmp
import logging
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psutil

from htmlcmp.common import compare_files
from htmlcmp.html_render_diff import get_browser

logger = logging.getLogger(__name__)


def _compare_loop(connection, driver, timeout):
    browser = None
    try:
        while True:
            request = connection.recv()
            if request is None:
                return
            a, b, kwargs = request
            if (
                browser is None
                and a.suffix == ".html"
                and not filecmp.cmp(a, b, shallow=False)
            ):
                browser = get_browser(driver, timeout=timeout)
            result = compare_files(a, b, browser=browser, **kwargs)
            connection.send((True, result))
    except Exception as exc:
        connection.send((False, f"{type(exc).__name__}: {exc}"))
        # Keep the process tree attached until the parent discards this worker.
        connection.recv()
    finally:
        if browser is not None:
            browser.quit()
        connection.close()


class ComparisonWorker:
    def __init__(self, driver, timeout=120.0):
        self.driver = driver
        self.timeout = timeout
        self._process = None
        self._connection = None

    def _start(self):
        context = multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        self._process = context.Process(
            target=_compare_loop, args=(child, self.driver, self.timeout), daemon=True
        )
        try:
            self._process.start()
        finally:
            child.close()

    def compare(self, a, b, **kwargs):
        started = time.monotonic()
        try:
            if self._process is None:
                self._start()
            self._connection.send((a, b, kwargs))
            remaining = max(0, self.timeout - (time.monotonic() - started))
            if not self._connection.poll(remaining):
                raise TimeoutError(
                    f"Comparison exceeded {self.timeout:g}s; browser discarded"
                )
            success, result = self._connection.recv()
            if not success:
                raise RuntimeError(result)
            return result
        except BaseException:
            self.close(abort=True)
            raise

    def close(self, *, abort=False):
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if process is None:
            return
        descendants = []
        try:
            if process.pid is not None:
                try:
                    descendants = psutil.Process(process.pid).children(recursive=True)
                except psutil.NoSuchProcess:
                    pass
                except psutil.AccessDenied:
                    logger.warning("Cannot inspect children of worker %s", process.pid)
            if not abort and process.is_alive():
                try:
                    connection.send(None)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                process.join(timeout=2)
            # Selenium's quit/service.stop can themselves hang. Never wait for
            # their transport timeout, and include the browser's child processes.
            for child in reversed(descendants):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
                except psutil.AccessDenied:
                    logger.warning("Cannot kill browser process %s", child.pid)
            if process.is_alive():
                process.kill()
            if process.pid is not None:
                process.join(timeout=2)
            psutil.wait_procs(descendants, timeout=2)
        finally:
            if connection is not None:
                connection.close()
            if process.pid is None or not process.is_alive():
                process.close()


class ComparisonExecutor(ThreadPoolExecutor):
    def __init__(self, max_workers, driver, timeout=120.0):
        self._driver = driver
        self._timeout = timeout
        self._local = threading.local()
        self._workers = []
        self._workers_lock = threading.Lock()
        super().__init__(max_workers=max_workers)

    def compare(self, a, b, **kwargs):
        if self._driver is None:
            return compare_files(a, b, **kwargs)
        worker = getattr(self._local, "worker", None)
        if worker is None:
            worker = ComparisonWorker(self._driver, self._timeout)
            self._local.worker = worker
            with self._workers_lock:
                self._workers.append(worker)
        return worker.compare(a, b, **kwargs)

    def shutdown(self, wait=True, *, cancel_futures=False):
        # A worker's deadline bounds this wait, including browser startup.
        super().shutdown(wait=wait, cancel_futures=cancel_futures)
        if wait:
            for worker in self._workers:
                worker.close()
