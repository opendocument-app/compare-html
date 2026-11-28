#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import argparse
import io
import math
import time
from pathlib import Path

from PIL import Image, ImageChops
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait

#: How long `settle` waits for the page by default, in seconds.
SETTLE_TIMEOUT = 10.0


def to_url(path: str | Path) -> str:
    if not isinstance(path, (str, Path)):
        raise TypeError(f"Expected str or Path, got {type(path)}")

    if isinstance(path, Path):
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return path.resolve().as_uri()
    return path


def screenshot(
    browser: webdriver.Remote,
    url: str,
    settling_time: float = 0,
    settle_timeout: float = SETTLE_TIMEOUT,
) -> Image.Image:
    if not isinstance(url, str):
        raise TypeError(f"Expected str, got {type(url)}")
    if not isinstance(browser, webdriver.Remote):
        raise TypeError(f"Expected webdriver.Remote, got {type(browser)}")

    browser.get(url)

    target_find_by = By.TAG_NAME
    target = "body"

    web_driver_wait = WebDriverWait(browser, 10)
    web_driver_wait.until(
        expected_conditions.presence_of_element_located((target_find_by, target))
    )
    web_driver_wait.until(
        lambda driver: driver.execute_script("return document.readyState") == "complete"
    )

    settle(browser, settling_time, settle_timeout)

    png = browser.get_screenshot_as_png()
    return Image.open(io.BytesIO(png))


#: Waits until the page has stopped changing, then for two frames, and answers
#: when that is done or the deadline passes.
#:
#: - `readyState` does not cover a web font: the load event fires while the face
#:   is still arriving, and the text is laid out again once it lands.
#: - A page that keeps laying itself out after load marks what is still changing
#:   with `aria-busy="true"` (WAI-ARIA), and clears it once that is done.
#: - A finite animation, such as a fade-in, is waited out; an infinite one never
#:   ends, so it is left running.
#:
#: Two frames then say a paint has happened rather than merely been asked for.
#: The checks repeat until one round finds nothing to wait for, since finishing
#: one kind of work can start another.
_SETTLE = """
const done = arguments[arguments.length - 1];
const deadline = performance.now() + arguments[0];
const left = () => Math.max(0, deadline - performance.now());
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
const busy = () => document.querySelector('[aria-busy="true"]') !== null;
const animations = () =>
  document.getAnimations
    ? document.getAnimations().filter((animation) => {
        const timing = animation.effect && animation.effect.getComputedTiming();
        return (
          animation.playState === "running" &&
          timing !== null &&
          timing.endTime !== Infinity
        );
      })
    : [];
(async () => {
  if (document.fonts) {
    await Promise.race([document.fonts.ready.catch(() => {}), sleep(left())]);
  }
  for (;;) {
    while (busy() && left() > 0) await sleep(50);
    const running = animations();
    if (running.length) {
      await Promise.race([
        Promise.all(running.map((a) => a.finished.catch(() => {}))),
        sleep(left()),
      ]);
    }
    await frame();
    await frame();
    if (left() === 0 || (!busy() && animations().length === 0)) break;
  }
  done(true);
})();
"""


def settle(
    browser: webdriver.Remote,
    settling_time: float = 0,
    settle_timeout: float = SETTLE_TIMEOUT,
) -> None:
    """Waits until the page has finished painting what it loaded.

    A screenshot taken before that catches the page mid-render, which is what
    makes an otherwise identical pair compare as different from one run to the
    next. A page still busy at the deadline is screenshotted as it is.
    """
    if not isinstance(browser, webdriver.Remote):
        raise TypeError(f"Expected webdriver.Remote, got {type(browser)}")

    # WebDriver gets a second more than the page, so the page gives up first
    # and the screenshot follows either way.
    browser.set_script_timeout(settle_timeout + 1)
    try:
        browser.execute_async_script(_SETTLE, settle_timeout * 1000)
    except WebDriverException:
        # an old driver without async scripts still gets the sleep below
        pass

    if settling_time:
        time.sleep(settling_time)


def add_settle_timeout_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--settle-timeout",
        type=float,
        default=SETTLE_TIMEOUT,
        help="Seconds to wait for a page to settle before its screenshot is "
        f"taken as it is (default: {SETTLE_TIMEOUT:g})",
    )


def check_settle_timeout(parser: argparse.ArgumentParser, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        parser.error("--settle-timeout must be a positive finite number")


def content_bottom(image: Image.Image) -> int:
    """Row just below the last pixel that differs from the page background.

    The background is sampled from the bottom right corner, so a page that
    paints its whole window (a full-height gradient, say) simply yields the
    full height and nothing is cropped.
    """
    background = image.getpixel((image.width - 1, image.height - 1))
    canvas = Image.new(image.mode, image.size, background)
    bbox = ImageChops.difference(image, canvas).getbbox()
    return 0 if bbox is None else bbox[3]


def get_browser(
    driver: str,
    max_width: int = 1000,
    max_height: int = 10000,
    *,
    timeout: float | None = None,
) -> webdriver.Remote:
    if not isinstance(driver, str):
        raise TypeError(f"Expected str, got {type(driver)}")
    if not isinstance(max_width, int) or not isinstance(max_height, int):
        raise TypeError(
            f"Expected int for max_width and max_height, got {type(max_width)} and {type(max_height)}"
        )

    if driver == "firefox":
        options = webdriver.FirefoxOptions()
        options.add_argument("--headless")
        browser = webdriver.Firefox(options=options)
    elif driver == "chrome":
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        browser = webdriver.Chrome(options=options)
    else:
        raise ValueError(f"Unsupported driver: {driver}")
    # Local WebDriver constructors do not expose ClientConfig. Disable urllib3's
    # automatic GET retries on their pool: a stalled screenshot must fail once.
    browser.command_executor._conn.connection_pool_kw["retries"] = 0
    if timeout is not None:
        # The CLI's parent process enforces the whole comparison deadline.
        browser.command_executor._client_config.timeout = timeout
        browser.set_page_load_timeout(timeout)
    browser.set_window_size(max_width, max_height)
    return browser


def html_render_diff(
    a: str | Path,
    b: str | Path,
    browser: webdriver.Remote,
    browser_b: webdriver.Remote = None,
    settle_timeout: float = SETTLE_TIMEOUT,
) -> tuple[Image.Image, tuple[Image.Image, Image.Image]]:
    if not isinstance(a, (str, Path)) or not isinstance(b, (str, Path)):
        raise TypeError("Both a and b must be str or Path instances")
    if not isinstance(browser, webdriver.Remote):
        raise TypeError(f"Expected webdriver.Remote, got {type(browser)}")
    if browser_b is None:
        browser_b = browser
    elif not isinstance(browser_b, webdriver.Remote):
        raise TypeError(f"Expected webdriver.Remote, got {type(browser_b)}")

    image_a = screenshot(browser, to_url(a), settle_timeout=settle_timeout).convert(
        "RGB"
    )
    image_b = screenshot(browser_b, to_url(b), settle_timeout=settle_timeout).convert(
        "RGB"
    )

    # The browser window is deliberately very tall so that long pages fit into
    # one screenshot, which leaves most shots mostly empty. That empty tail is
    # identical on both sides by construction and would otherwise dominate
    # every position and area derived from the diff, so it is cropped away.
    height = max(content_bottom(image_a), content_bottom(image_b), 1)
    if height < min(image_a.height, image_b.height):
        image_a = image_a.crop((0, 0, image_a.width, height))
        image_b = image_b.crop((0, 0, image_b.width, height))

    diff = ImageChops.difference(image_a, image_b)
    return diff, (image_a, image_b)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("a", type=Path, help="Path to the first HTML file")
    parser.add_argument("b", type=Path, help="Path to the second HTML file")
    parser.add_argument("--driver", choices=["chrome", "firefox"], default="firefox")
    parser.add_argument("--max-width", type=int, default=1000)
    parser.add_argument("--max-height", type=int, default=10000)
    add_settle_timeout_argument(parser)
    args = parser.parse_args()
    check_settle_timeout(parser, args.settle_timeout)

    browser = get_browser(args.driver, args.max_width, args.max_height)
    diff, _ = html_render_diff(
        args.a, args.b, browser, settle_timeout=args.settle_timeout
    )
    browser.quit()
    bounding_box = diff.getbbox()

    if bounding_box:
        print("images are different")
        print("first error at %f%%" % (1e2 * bounding_box[1] / diff.height))
        print(
            "bounding box %f%%"
            % (
                1e2
                * (
                    (bounding_box[2] - bounding_box[0])
                    * (bounding_box[3] - bounding_box[1])
                )
                / (diff.width * diff.height)
            )
        )
        return 1

    print("images are the same")
    return 0


if __name__ == "__main__":
    sys.exit(main())
