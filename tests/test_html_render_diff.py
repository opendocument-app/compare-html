import pytest
from pathlib import Path
import shutil

from htmlcmp.html_render_diff import to_url, get_browser, html_render_diff


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

    # Test with PhantomJS
    if shutil.which("phantomjs") is not None:
        browser = get_browser("phantomjs")
        assert browser.name == "phantomjs"
        browser.quit()


def test_html_render_diff():
    test1 = Path(__file__).parent / "test1.html"
    test2 = Path(__file__).parent / "test2.html"

    with pytest.raises(TypeError):
        html_render_diff(None, None, None)
    with pytest.raises(TypeError):
        html_render_diff(str(test1), str(test2), None)

    diff, _ = html_render_diff(test1, test1, get_browser("firefox"))
    assert diff.getbbox() is None
    diff, _ = html_render_diff(test1, test2, get_browser("firefox"))
    assert diff.getbbox() is not None
