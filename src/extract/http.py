"""One polite HTTP GET used by both API extractors.

Rules from the project brief: honest User-Agent, a timeout on every request, retries with
growing waits (backoff), and clear handling of HTTP errors and rate limits.
"""
import time

import requests

USER_AGENT = "DSC620-olist-student-project/0.1 (university coursework, LAU)"
TIMEOUT_SECONDS = 30
MAX_WAIT_SECONDS = 60  # never sleep longer than this, even if the server asks


class ExtractError(Exception):
    """Raised when an extraction cannot be completed. The message says what went wrong."""


def http_get(url, params=None, tries=4, getter=requests.get, sleep=time.sleep):
    """GET url and return the response, or raise ExtractError with a clear message.

    - Timeouts, connection errors, HTTP 429 (rate limit) and HTTP 5xx are retried,
      waiting 2, 4, 8 ... seconds (or the server's Retry-After if it sends one).
    - Any other HTTP error (400, 404 ...) is our mistake, so retrying is pointless: stop at once.
    getter and sleep can be replaced in tests so no real network or waiting is needed.
    """
    last_problem = "no attempt made"
    for attempt in range(1, tries + 1):
        wait = 2 ** attempt
        try:
            response = getter(url, params=params, timeout=TIMEOUT_SECONDS,
                              headers={"User-Agent": USER_AGENT})
        except (requests.Timeout, requests.ConnectionError) as err:
            last_problem = f"{type(err).__name__}: {err}"
        else:
            status = response.status_code
            if status < 400:
                return response
            if status == 429 or status >= 500:
                last_problem = f"HTTP {status}"
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():  # the server told us how long to wait
                    wait = int(retry_after)
            else:
                raise ExtractError(f"{url} returned HTTP {status}: {response.text[:200]}")
        if attempt < tries:
            print(f"  attempt {attempt} failed ({last_problem}); waiting {min(wait, MAX_WAIT_SECONDS)}s")
            sleep(min(wait, MAX_WAIT_SECONDS))
    raise ExtractError(f"{url} failed after {tries} attempts (last problem: {last_problem})")
