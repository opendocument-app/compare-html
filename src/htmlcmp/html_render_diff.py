#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import shutil
import sys
import argparse
import io
import time
from pathlib import Path

from PIL import Image, ImageChops
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait


def to_url(path: str | Path) -> str:
    if not isinstance(path, (str, Path)):
        raise TypeError(f"Expected str or Path, got {type(path)}")

    if isinstance(path, Path):
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return path.resolve().as_uri()
    return path


def screenshot(browser: webdriver.Remote, url: str) -> Image.Image:
    if not isinstance(url, str):
        raise TypeError(f"Expected str, got {type(url)}")
    if not isinstance(browser, webdriver.Remote):
        raise TypeError(f"Expected webdriver.Remote, got {type(browser)}")

    browser.get(url)

    target_find_by = By.TAG_NAME
    target = "body"

    web_driver_wait = WebDriverWait(browser, 5)
    web_driver_wait.until(
        expected_conditions.presence_of_element_located((target_find_by, target))
    )
    web_driver_wait.until(
        lambda driver: driver.execute_script("return document.readyState") == "complete"
    )

    png = browser.get_screenshot_as_png()
    return Image.open(io.BytesIO(png))


def get_browser(
    driver: str, max_width: int = 1000, max_height: int = 10000
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
    browser.set_window_size(max_width, max_height)
    return browser


def html_render_diff(
    a: str | Path, b: str | Path, browser: webdriver.Remote
) -> tuple[Image.Image, tuple[Image.Image, Image.Image]]:
    if not isinstance(a, (str, Path)) or not isinstance(b, (str, Path)):
        raise TypeError("Both a and b must be str or Path instances")
    if not isinstance(browser, webdriver.Remote):
        raise TypeError(f"Expected webdriver.Remote, got {type(browser)}")

    image_a = screenshot(browser, to_url(a))
    image_b = screenshot(browser, to_url(b))

    image_a = image_a.convert("RGB")
    image_b = image_b.convert("RGB")
    diff = ImageChops.difference(image_a, image_b)
    # safe files to current directory
    image_a.save('a.png')
    image_b.save('b.png')
    diff.save('diffab.png')
    return diff, (image_a, image_b)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("a", type=Path, help="Path to the first HTML file")
    parser.add_argument("b", type=Path, help="Path to the second HTML file")
    parser.add_argument("--driver", choices=["chrome", "firefox"], default="firefox")
    parser.add_argument("--max-width", default=1000)
    parser.add_argument("--max-height", default=10000)
    args = parser.parse_args()

    browser = get_browser(args.driver, args.max_width, args.max_height)
    diff, _ = html_render_diff(args.a, args.b, browser)
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
