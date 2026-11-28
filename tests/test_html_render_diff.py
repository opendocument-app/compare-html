import pytest
from pathlib import Path
import shutil

import time

from htmlcmp.html_render_diff import (
    to_url,
    get_browser,
    html_render_diff,
    screenshot,
)


def test_to_url():
    with pytest.raises(TypeError):
        to_url(None)
    with pytest.raises(TypeError):
        to_url(1)
    with pytest.raises(FileNotFoundError):
        to_url(Path(__file__).parent)
    assert to_url("file:///path/to/file") == "file:///path/to/file"
    assert to_url("https://example.com") == "https://example.com"
    assert to_url(Path(__file__))


def test_get_browser():
    with pytest.raises(ValueError):
        get_browser("unsupported_driver")

    # Test with Chrome
    browser = get_browser("chrome")
    assert browser.name == "chrome"
    browser.quit()

    # Test with Firefox
    browser = get_browser("firefox")
    assert browser.name == "firefox"
    browser.quit()


def test_html_render_diff():
    test1 = Path(__file__).parent / "test1.html"
    test2 = Path(__file__).parent / "test2.html"

    with pytest.raises(TypeError):
        html_render_diff(None, None, None)
    with pytest.raises(TypeError):
        html_render_diff(str(test1), str(test2), None)

    browser = get_browser("firefox")
    try:
        diff, _ = html_render_diff(test1, test1, browser)
        assert diff.getbbox() is None
        diff, _ = html_render_diff(test1, test2, browser)
        assert diff.getbbox() is not None
    finally:
        browser.quit()


@pytest.mark.parametrize("driver", ["chrome", "firefox"])
@pytest.mark.parametrize("page", ["busy.html", "animated.html"])
def test_screenshot_waits_for_the_page_to_settle(driver, page):
    here = Path(__file__).parent
    browser = get_browser(driver)
    try:
        diff, _ = html_render_diff(here / page, here / "settled.html", browser)
        assert diff.getbbox() is None
    finally:
        browser.quit()


@pytest.mark.parametrize("driver", ["chrome", "firefox"])
def test_screenshot_leaves_an_infinite_animation_running(driver):
    browser = get_browser(driver)
    try:
        started = time.monotonic()
        screenshot(browser, to_url(Path(__file__).parent / "spinning.html"))
        assert time.monotonic() - started < 5
    finally:
        browser.quit()


@pytest.mark.parametrize("driver", ["chrome", "firefox"])
def test_screenshot_stops_waiting_at_the_settle_timeout(driver):
    here = Path(__file__).parent
    browser = get_browser(driver)
    try:
        diff, _ = html_render_diff(
            here / "busy.html", here / "settled.html", browser, settle_timeout=0.3
        )
        assert diff.getbbox() is not None
    finally:
        browser.quit()
