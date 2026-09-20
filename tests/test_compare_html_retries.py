"""A mismatch is rendered again before it is reported.

A browser gives no promise that two runs of the same page paint alike at the
same instant, so a single mismatching render does not say the two files differ.
These drive `compare_html` over a stub renderer, because what is under test is
what it does with a mismatch rather than how a page paints.
"""

from pathlib import Path

import pytest
from PIL import Image

from htmlcmp import common

TEST1 = Path(__file__).parent / "test1.html"


def images(same: bool):
    """A `html_render_diff` result that says the pair matches, or does not."""
    diff = Image.new("RGB", (4, 4), (0, 0, 0) if same else (255, 0, 0))
    return diff, (Image.new("RGB", (4, 4)), Image.new("RGB", (4, 4)))


def renderer(pattern, calls):
    """Stands in for `html_render_diff`, answering `pattern` in turn."""

    def render(a, b, browser=None):
        calls.append((a, b))
        return images(pattern[min(len(calls) - 1, len(pattern) - 1)])

    return render


def test_a_mismatch_that_does_not_come_back_is_a_match(monkeypatch):
    calls = []
    monkeypatch.setattr(common, "html_render_diff", renderer([False, True], calls))

    assert common.compare_html(TEST1, TEST1, browser=object()) is True
    assert len(calls) == 2


def test_a_mismatch_that_comes_back_is_reported(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(common, "html_render_diff", renderer([False], calls))

    assert (
        common.compare_html(TEST1, TEST1, browser=object(), diff_output=tmp_path)
        is False
    )
    assert len(calls) == 2
    assert (tmp_path / "a.png").is_file()
    assert (tmp_path / "b.png").is_file()
    assert (tmp_path / "diff.png").is_file()


def test_a_match_is_rendered_once(monkeypatch):
    calls = []
    monkeypatch.setattr(common, "html_render_diff", renderer([True], calls))

    assert common.compare_html(TEST1, TEST1, browser=object()) is True
    assert len(calls) == 1


def test_retries_zero_reports_the_first_render(monkeypatch):
    calls = []
    monkeypatch.setattr(common, "html_render_diff", renderer([False, True], calls))

    assert common.compare_html(TEST1, TEST1, browser=object(), retries=0) is False
    assert len(calls) == 1


def test_retries_is_a_count(monkeypatch):
    calls = []
    monkeypatch.setattr(common, "html_render_diff", renderer([True], calls))

    with pytest.raises(ValueError):
        common.compare_html(TEST1, TEST1, browser=object(), retries=-1)
    with pytest.raises(ValueError):
        common.compare_html(TEST1, TEST1, browser=object(), retries="two")
