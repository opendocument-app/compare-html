#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import argparse
import json
import subprocess
import shlex
from pathlib import Path

from htmlcmp.common import bcolors


def tidy_json(path):
    try:
        with open(path, "r") as f:
            json.load(f)
        return 0
    except ValueError:
        return 1


def tidy_html(path, tidy_config=None):
    if tidy_config:
        cmd = shlex.split(f'tidy -config "{tidy_config.resove()}" -q "{path}"')
    else:
        cmd = shlex.split(f'tidy -q "{path}"')
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode == 1:
        return 1
    if result.returncode > 1:
        return 2
    return 0


def tidy_file(path, tidy_config=None):
    if path.suffix == ".json":
        return tidy_json(path)
    elif path.suffix == ".html":
        return tidy_html(path, tidy_config=tidy_config)


def tidyable_file(path):
    if path.suffix == ".json":
        return True
    if path.suffix == ".html":
        return True
    return False


def tidy_dir(path, level=0, prefix="", tidy_config=None):
    prefix_file = prefix + "├── "
    if level == 0:
        print(f"tidy dir {path}")

    result = {
        "warning": [],
        "error": [],
    }

    items = [path / name for name in path.iterdir()]
    files = sorted(
        [path for path in items if path.is_file() and tidyable_file(path)]
    )
    dirs = sorted([path for path in items if path.is_dir()])

    for filename in [path.name for path in files]:
        filepath = path / filename
        tidy = tidy_file(filepath, tidy_config=tidy_config)
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
            tidy_config=tidy_config,
        )
        result["warning"].extend(subresult["warning"])
        result["error"].extend(subresult["error"])

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="Path to directory to tidy")
    parser.add_argument("--html-tidy-config", type=Path, help="Path to tidy config file")
    args = parser.parse_args()

    result = tidy_dir(args.path, tidy_config=args.tidy_config)
    if result["error"]:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
