"""A file or directory on one side only is a failure, named below the roots."""

from pathlib import Path

from htmlcmp.compare_output_cli import collect_tasks


def write(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<html></html>")


def test_one_sided_entries_are_named_below_the_roots(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write(a / "both" / "page.html")
    write(b / "both" / "page.html")
    write(a / "both" / "only_a.html")
    write(b / "both" / "only_b.html")
    write(a / "dir_a" / "page.html")
    write(b / "dir_b" / "page.html")

    tasks, failures = collect_tasks(a, b)

    assert [task.rel for task in tasks] == [Path("both/page.html")]
    assert sorted((str(f.rel), f.reason) for f in failures) == [
        ("both/only_a.html", "missing in monitored (B)"),
        ("both/only_b.html", "missing in reference (A)"),
        ("dir_a", "missing in monitored (B)"),
        ("dir_b", "missing in reference (A)"),
    ]
