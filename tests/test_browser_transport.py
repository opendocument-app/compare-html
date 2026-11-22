"""A stalled screenshot gets one HTTP request, not four timeout periods."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest
from selenium.webdriver.remote.client_config import ClientConfig
from selenium.webdriver.remote.command import Command
from selenium.webdriver.remote.remote_connection import RemoteConnection
from urllib3.exceptions import MaxRetryError, ReadTimeoutError

from htmlcmp import html_render_diff as render


@pytest.mark.parametrize(
    "driver,factory", [("firefox", "Firefox"), ("chrome", "Chrome")]
)
def test_screenshot_transport_does_not_retry(monkeypatch, driver, factory):
    requests = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            release.wait(5)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = RemoteConnection(
        client_config=ClientConfig(
            remote_server_addr=f"http://127.0.0.1:{server.server_port}", timeout=0.1
        )
    )
    browser = Mock(command_executor=connection)
    monkeypatch.setattr(render.webdriver, factory, Mock(return_value=browser))
    try:
        assert render.get_browser(driver) is browser
        with pytest.raises((MaxRetryError, ReadTimeoutError)):
            connection.execute(Command.SCREENSHOT, {"sessionId": "test"})
        assert requests == ["/session/test/screenshot"]
    finally:
        release.set()
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()
