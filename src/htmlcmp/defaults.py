"""Defaults shared by the library functions and the command line tools."""

#: Browser used to render HTML.
DRIVER = "firefox"
#: Browser window size in pixels; the height fits long pages in one shot.
MAX_WIDTH = 1000
MAX_HEIGHT = 10000
#: Parallel comparison or tidy workers.
MAX_WORKERS = 1
#: How many times a mismatch is rendered again before it is reported.
RETRIES = 1
#: Deadline in seconds per browser comparison.
TIMEOUT = 120.0
#: How long `settle` waits for the page, in seconds.
SETTLE_TIMEOUT = 10.0
