import sys
import json
import logging
import filecmp
from pathlib import Path

from htmlcmp.html_render_diff import get_browser, html_render_diff


class bcolors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


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
        browser = get_browser("firefox")
    diff, (image_a, image_b) = html_render_diff(a, b, browser=browser)
    result = diff.getbbox() is None
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

    if filecmp.cmp(a, b, shallow=False):
        return True
    if a.suffix == ".json":
        return compare_json(a, b)
    if a.suffix == ".html":
        if kwargs.get("browser") is None:
            # No browser available (e.g. --driver none): the files already
            # differ at the byte level and we cannot render to check for visual
            # equality, so report them as different rather than spinning up a
            # browser here.
            return False
        return compare_html(a, b, **kwargs)
    return False


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


def verbosity_to_level(verbosity: int) -> int:
    if verbosity >= 3:
        return logging.DEBUG
    elif verbosity == 2:
        return logging.INFO
    elif verbosity == 1:
        return logging.WARNING
    else:
        return logging.ERROR


def setup_logging(
    verbosity: int, log_file: Path = None, log_file_verbosity: int = None
) -> None:
    level = verbosity_to_level(verbosity)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_file is not None:
        file_level = (
            verbosity_to_level(log_file_verbosity)
            if log_file_verbosity is not None
            else level
        )

        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(file_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
