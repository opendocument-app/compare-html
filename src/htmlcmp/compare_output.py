#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import argparse
import json
import threading
import filecmp
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from htmlcmp.html_render_diff import get_browser, html_render_diff
from htmlcmp.common import bcolors


class Config:
    thread_local = threading.local()


def parse_json(path: Path) -> dict:
    if not isinstance(path, Path):
        raise TypeError(f"Expected Path, got {type(path)}")
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    with open(path) as f:
        return json.load(f)


def compare_json(a: Path, b: Path) -> bool:
    if not isinstance(a, Path) or not isinstance(b, Path):
        raise TypeError("Both arguments must be of type Path")
    if not a.is_file() or not b.is_file():
        raise FileNotFoundError("Both arguments must be files")

    json_a = json.dumps(parse_json(a), sort_keys=True)
    json_b = json.dumps(parse_json(b), sort_keys=True)
    return json_a == json_b


def compare_html(a: Path, b: Path, browser=None, diff_output: Path = None) -> bool:
    if not isinstance(a, Path) or not isinstance(b, Path):
        raise TypeError("Both arguments must be of type Path")
    if not a.is_file() or not b.is_file():
        raise FileNotFoundError("Both arguments must be files")

    if browser is None:
        browser = get_browser()
    diff, (image_a, image_b) = html_render_diff(a, b, browser=browser)
    result = True if diff.getbbox() is None else False
    if diff_output is not None and not result:
        diff_output.mkdir(parents=True, exist_ok=True)
        image_a.save(diff_output / "a.png")
        image_b.save(diff_output / "b.png")
        diff.save(diff_output / "diff.png")
    return result


def compare_files(a: Path, b: Path, **kwargs) -> bool:
    if not isinstance(a, Path) or not isinstance(b, Path):
        raise TypeError("Both arguments must be of type Path")
    if not a.is_file() or not b.is_file():
        raise FileNotFoundError("Both arguments must be files")

    if filecmp.cmp(a, b):
        return True
    if a.suffix == ".json":
        return compare_json(a, b)
    if a.suffix == ".html":
        return compare_html(a, b, **kwargs)


def comparable_file(path: Path) -> bool:
    if not isinstance(path, Path):
        raise TypeError(f"Expected Path, got {type(path)}")
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    if path.suffix == ".json":
        return True
    if path.suffix == ".html":
        return True
    return False


def submit_compare_dirs(
    a: Path, b: Path, executor, diff_output: Path = None, **kwargs
) -> dict[str, list[Path]]:
    if not isinstance(a, Path) or not isinstance(b, Path):
        raise TypeError("Both arguments must be of type Path")
    if not a.is_dir() or not b.is_dir():
        raise FileNotFoundError("Both arguments must be directories")

    results = {
        "common_dirs": [],
        "common_files": [],
        "left_files_missing": [],
        "right_files_missing": [],
        "left_dirs_missing": [],
        "right_dirs_missing": [],
    }

    left = sorted(p.name for p in a.iterdir())
    right = sorted(p.name for p in b.iterdir())

    left_files = sorted(
        [name for name in left if (a / name).is_file() and comparable_file(a / name)]
    )
    right_files = sorted(
        [name for name in right if (b / name).is_file() and comparable_file(b / name)]
    )
    common_files = [name for name in left_files if name in right_files]

    def compare(path_a, path_b, diff_output):
        browser = getattr(Config.thread_local, "browser", None)
        return compare_files(path_a, path_b, browser=browser, diff_output=diff_output)

    for name in common_files:
        future = executor.submit(
            compare,
            a / name,
            b / name,
            diff_output=(None if diff_output is None else diff_output / name),
        )
        results["common_files"].append((name, future))

    results["left_files_missing"] = [
        name for name in right_files if name not in left_files
    ]
    results["right_files_missing"] = [
        name for name in left_files if name not in right_files
    ]

    left_dirs = sorted([name for name in left if (a / name).is_dir()])
    right_dirs = sorted([name for name in right if (b / name).is_dir()])
    common_dirs = [path for path in left_dirs if path in right_dirs]

    for name in common_dirs:
        sub_results = submit_compare_dirs(
            a / name,
            b / name,
            executor=executor,
            diff_output=(None if diff_output is None else diff_output / name),
            **kwargs,
        )
        results["common_dirs"].append((name, sub_results))

    results["left_dirs_missing"] = [
        name for name in right_dirs if name not in left_dirs
    ]
    results["right_dirs_missing"] = [
        name for name in left_dirs if name not in right_dirs
    ]

    return results


def print_results(
    results: dict[str, list[Path]], a: Path, b: Path, level: int = 0, prefix: str = ""
) -> dict[str, list[Path]]:
    if not isinstance(a, Path) or not isinstance(b, Path):
        raise TypeError("Both arguments must be of type Path")
    if not a.is_dir() or not b.is_dir():
        raise FileNotFoundError("Both arguments must be directories")
    if not isinstance(results, dict):
        raise TypeError("Results must be a dictionary")

    prefix_file = prefix + "├── "
    if level == 0:
        print(f"compare dir {a} with {b}")

    result = {
        "left_files_missing": [],
        "right_files_missing": [],
        "left_dirs_missing": [],
        "right_dirs_missing": [],
        "files_different": [],
    }

    left_files_missing = " ".join(results["left_files_missing"])
    if left_files_missing:
        print(
            f"{prefix_file}{bcolors.FAIL}missing files left: {left_files_missing} ✘{bcolors.ENDC}"
        )
        result["left_files_missing"].extend(
            [a / name for name in results["left_files_missing"]]
        )
    right_files_missing = " ".join(results["right_files_missing"])
    if right_files_missing:
        print(
            f"{prefix_file}{bcolors.FAIL}missing files right: {right_files_missing} ✘{bcolors.ENDC}"
        )
        result["right_files_missing"].extend(
            [a / name for name in results["right_files_missing"]]
        )

    for name, future in results["common_files"]:
        cmp = future.result()
        if cmp:
            print(f"{prefix_file}{bcolors.OKGREEN}{name} ✓{bcolors.ENDC}")
        else:
            print(f"{prefix_file}{bcolors.FAIL}{name} ✘{bcolors.ENDC}")
            result["files_different"].append(a / name)

    left_dirs_missing = " ".join(results["left_dirs_missing"])
    if left_dirs_missing:
        print(
            f"{prefix_file}{bcolors.FAIL}missing dirs left: {left_dirs_missing} ✘{bcolors.ENDC}"
        )
        result["left_dirs_missing"].extend(
            [a / name for name in results["left_dirs_missing"]]
        )
    right_dirs_missing = " ".join(results["right_dirs_missing"])
    if right_dirs_missing:
        print(
            f"{prefix_file}{bcolors.FAIL}missing dirs right: {right_dirs_missing} ✘{bcolors.ENDC}"
        )
        result["right_dirs_missing"].extend(
            [a / name for name in results["right_dirs_missing"]]
        )

    for name, sub_results in results["common_dirs"]:
        print(prefix + "├── " + name)
        sub_result = print_results(
            sub_results,
            a / name,
            b / name,
            level=level + 1,
            prefix=prefix + "│   ",
        )
        result["left_files_missing"].extend(sub_result["left_files_missing"])
        result["right_files_missing"].extend(sub_result["right_files_missing"])
        result["left_dirs_missing"].extend(sub_result["left_dirs_missing"])
        result["right_dirs_missing"].extend(sub_result["right_dirs_missing"])
        result["files_different"].extend(sub_result["files_different"])

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("a", type=Path, help="Path to the first directory")
    parser.add_argument("b", type=Path, help="Path to the second directory")
    parser.add_argument(
        "--driver", choices=["chrome", "firefox", "phantomjs"], default="firefox"
    )
    parser.add_argument(
        "--diff-output", type=Path, help="Output directory for diff images"
    )
    parser.add_argument("--max-workers", type=int, default=1)
    args = parser.parse_args()

    def initializer():
        browser = getattr(Config.thread_local, "browser", None)
        if browser is None:
            browser = get_browser(driver=args.driver)
            Config.thread_local.browser = browser

    executor = ThreadPoolExecutor(max_workers=args.max_workers, initializer=initializer)

    results = submit_compare_dirs(
        args.a, args.b, executor=executor, diff_output=args.diff_output
    )

    result = print_results(results, args.a, args.b)
    if (
        result["left_files_missing"]
        or result["right_files_missing"]
        or result["left_dirs_missing"]
        or result["right_dirs_missing"]
        or result["files_different"]
    ):
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
