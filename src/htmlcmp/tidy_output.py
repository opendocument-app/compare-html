#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import argparse
import json
import subprocess
from pathlib import Path

from htmlcmp.common import bcolors


def tidy_json(path: Path) -> int:
    if not isinstance(path, Path):
        raise TypeError("path must be a Path object")
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not a file")

    try:
        with open(path, "r") as f:
            json.load(f)
        return 0
    except ValueError:
        return 1


def tidy_html(path: Path, html_tidy_config: Path = None) -> int:
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
    if result.returncode == 1:
        return 1
    if result.returncode > 1:
        return 2
    return 0


def tidy_file(path: Path, html_tidy_config: Path = None) -> int:
    if not isinstance(path, Path):
        raise TypeError("path must be a Path object")
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not a file")

    if path.suffix == ".json":
        return tidy_json(path)
    elif path.suffix == ".html":
        return tidy_html(path, html_tidy_config=html_tidy_config)


def tidyable_file(path: Path) -> bool:
    if not isinstance(path, Path):
        raise TypeError("path must be a Path object")
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not a file")

    if path.suffix == ".json":
        return True
    if path.suffix == ".html":
        return True
    return False


def tidy_dir(
    path: Path, level: int = 0, prefix: str = "", html_tidy_config: Path = None
) -> dict[str, list[Path]]:
    if not isinstance(path, Path):
        raise TypeError("path must be a Path object")
    if not path.is_dir():
        raise NotADirectoryError(f"{path} is not a directory")
    if not isinstance(level, int) or level < 0:
        raise ValueError("level must be a non-negative integer")
    if not isinstance(prefix, str):
        raise TypeError("prefix must be a string")
    if html_tidy_config is not None and not isinstance(html_tidy_config, Path):
        raise TypeError("html_tidy_config must be a Path object or None")
    if html_tidy_config is not None and not html_tidy_config.is_file():
        raise FileNotFoundError(f"{html_tidy_config} is not a file")

    prefix_file = prefix + "├── "
    if level == 0:
        print(f"tidy dir {path}")

    result = {
        "warning": [],
        "error": [],
    }

    items = [p for p in path.iterdir()]
    files = sorted([path for path in items if path.is_file() and tidyable_file(path)])
    dirs = sorted([path for path in items if path.is_dir()])

    for filename in [path.name for path in files]:
        filepath = path / filename
        tidy = tidy_file(filepath, html_tidy_config=html_tidy_config)
        if tidy == 0:
            print(f"{prefix_file}{bcolors.OKGREEN}{filename} ✓{bcolors.ENDC}")
        elif tidy == 1:
            print(f"{prefix_file}{bcolors.WARNING}{filename} ✓{bcolors.ENDC}")
            result["warning"].append(filepath)
        elif tidy > 1:
            print(f"{prefix_file}{bcolors.FAIL}{filename} ✘{bcolors.ENDC}")
            result["error"].append(filepath)

    for dirname in [path.name for path in dirs]:
        print(prefix + "├── " + dirname)
        subresult = tidy_dir(
            path / dirname,
            level=level + 1,
            prefix=prefix + "│   ",
            html_tidy_config=html_tidy_config,
        )
        result["warning"].extend(subresult["warning"])
        result["error"].extend(subresult["error"])

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="Path to directory to tidy")
    parser.add_argument(
        "--html-tidy-config", type=Path, help="Path to tidy config file"
    )
    args = parser.parse_args()

    result = tidy_dir(args.path, html_tidy_config=args.html_tidy_config)
    if result["error"]:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
