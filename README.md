# htmlcmp

Tool to compare (generated) HTML files visually and automatically using Selenium.

Provides various entry points to run:
- `compare-html` is a CLI tool to compare two directory structures containing HTML files
- `compare-html-server` starts a webserver and allows to inspect differences manually
- `html-render-diff` renders two HTML files and produces images
- `html-tidy` allows to run HTML tidy on a directory

Used for regression testing in https://github.com/opendocument-app/OpenDocument.core.

## When a screenshot is taken

A page is screenshotted once it has stopped changing:

- the document has loaded and its web fonts have arrived
- no element carries `aria-busy="true"`, the WAI-ARIA way for a page that keeps
  laying itself out after load to say it is not done yet
- finite animations, such as a fade-in, have finished; an infinite one is left
  running
- two frames have been painted since

Waiting stops after 10 seconds, and the page is screenshotted as it is then.
`--settle-timeout` changes that limit in `compare-html`, `compare-html-server`
and `html-render-diff`.

## Browser timeouts in the CLI

`compare-html --timeout 120` limits each browser comparison to 120 seconds,
including browser startup, both screenshots, and any `--retries` of a pixel
mismatch. The deadline starts when a worker takes the file, not while it waits
in the queue. Use a larger positive value for especially expensive pages.
Each screenshot may wait up to `--settle-timeout` for its page, so keep
`--timeout` above twice that times one more than `--retries`.
The same value configures WebDriver's HTTP and page-load timeouts, so increasing
the deadline is not cut short by Selenium's default 120-second HTTP timeout.

A timeout is reported as an error and makes the command exit unsuccessfully.
The worker and its browser processes are discarded; the next file gets a fresh
browser. Successful comparisons reuse their browser. Shutdown also has bounded
cleanup, so an unresponsive Selenium `quit()` cannot block the command forever.
Cleanup can take a few seconds beyond the comparison deadline.

WebDriver HTTP requests are not automatically retried. `--retries` still
rechecks visual mismatches; it does not repeat a timed-out browser command.
This does not change the pixel comparison or hide errors from slow renderers.
The per-comparison deadline applies to `compare-html`, not the interactive
`compare-html-server`.

## Reviewing differences in the browser

`compare-html-server` serves two pages:

- the **overview** lists every comparable file with its status (`same`,
  `different`, `pending`), a search box, status filters with counts, and an
  `update ref` button per file plus `Update all in view`
- the **compare page** (click a path) shows reference (A) and monitored (B)
  side by side with synchronised scrolling, a diff strip in between, and the
  controls to work through a review

On the compare page:

- **Update reference** copies the monitored file over the reference one; the
  file is then marked `✓ accepted` for the rest of the session (the mark
  disappears again if the monitored file changes afterwards)
- **← / →** step to the previous and next document; tick *diffs only* to skip
  files that already match
- the **diff strip** maps the whole page into the column: red bars mark the
  regions that differ, the blue box marks the visible part of the document, and
  a click jumps both panes to that position

| key | action |
| --- | --- |
| `←` / `k`, `→` / `j` | previous / next document |
| `1` / `2` / `3` | reference only / side by side / monitored only |
| `d` | toggle the diff strip |
| `u` | update the reference |
| `r` | reload both panes |
| `Esc` | back to the overview |
| `?` | shortcut help |

Comparison results are computed live, so the pages update themselves while the
monitored directory is being regenerated. Accepted marks are kept in memory
only and are gone after a restart.

## Install via PyPI

```bash
pip install htmlcmp
```

## Download and run the docker image

```bash
docker pull ghcr.io/opendocument-app/odr_core_test
```

```bash
docker run -ti \
  -v $(pwd):/repo \
  -p 8000:8000 \
  ghcr.io/opendocument-app/odr_core_test \
  compare-html-server /repo/REFERENCE /repo/MONITORED --compare --driver firefox --port 8000
```

## Manually build the docker image

```bash
docker build --tag odr_core_test test/docker
```

## Run locally

```bash
PYTHONPATH=$(pwd)/src:$PYTHONPATH python ./src/htmlcmp/compare_output_server.py \
  /path/to/REFERENCE \
  /path/to/MONITORED \
  --compare \
  --driver firefox \
  --port 8000 \
  -vv
```
