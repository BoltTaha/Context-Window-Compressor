"""
Rate limiter for the Gemini API free tier.

Primary model   : gemini-2.5-flash      — 10 RPM,  250 RPD
Fallback model  : gemini-2.5-flash-lite — 15 RPM, 1000 RPD

Strategy:
  1. Sliding-window RPM guard  — waits before each call so we never exceed 10/min.
  2. Exponential-backoff retry — on 429 / 503 / network errors, retries up to
     MAX_RETRIES times with increasing waits (8→16→32→64s).
  3. Automatic fallback        — if primary model exhausts all retries on a 503,
     silently switches to the fallback model for that call.
"""

import time
import threading
import logging
from collections import deque

logger = logging.getLogger(__name__)

# ── tuneable constants ────────────────────────────────────────────────────────
RPM_LIMIT    = 10      # free-tier gemini-2.5-flash
MAX_RETRIES  = 4       # retry attempts per model before giving up / falling back
BASE_BACKOFF = 8       # seconds for first retry; doubles each attempt (8→16→32→64)
# ─────────────────────────────────────────────────────────────────────────────

_RETRYABLE = (
    "429",
    "503",
    "resource_exhausted",
    "unavailable",
    "connecterror",
    "name resolution",
    "connection",
    "timeout",
    "reset by peer",
    "high demand",
    "service unavailable",
    "servererror",
)

_503_SIGNALS = ("503", "unavailable", "high demand", "service unavailable", "servererror")


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw.lower() in msg for kw in _RETRYABLE)


def _is_503(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw.lower() in msg for kw in _503_SIGNALS)


class SlidingWindowRateLimiter:
    """
    Tracks request timestamps inside a 60-second sliding window.
    Blocks until making another request is within the RPM limit.
    Thread-safe.
    """

    def __init__(self, rpm: int):
        self.rpm = rpm
        self._lock = threading.Lock()
        self._timestamps: deque[float] = deque()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] >= 60.0:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.rpm:
                sleep_for = 60.0 - (now - self._timestamps[0]) + 0.1
                if sleep_for > 0:
                    logger.info("Rate limit: sleeping %.1fs to stay within %d RPM.", sleep_for, self.rpm)
                    time.sleep(sleep_for)
            self._timestamps.append(time.monotonic())


# Singleton — shared across all modules
_limiter = SlidingWindowRateLimiter(rpm=RPM_LIMIT)


def _try_model(client, model: str, contents: str, max_retries: int = MAX_RETRIES) -> str:
    """
    Attempt generate_content with retries.
    503 errors are NOT retried here — caller should switch to fallback immediately.
    429 / network errors are retried with exponential backoff.
    Raises the last exception on final failure.
    """
    last_exc = None
    for attempt in range(max_retries):
        _limiter.wait()
        try:
            response = client.models.generate_content(model=model, contents=contents)
            return response.text.strip()
        except Exception as exc:
            # 503 = server overloaded — don't retry, let caller switch to fallback
            if _is_503(exc):
                raise
            if _is_retryable(exc) and attempt < max_retries - 1:
                backoff = BASE_BACKOFF * (2 ** attempt)
                logger.warning(
                    "[%s] Retryable error attempt %d/%d (%s) — waiting %ds.",
                    model, attempt + 1, max_retries, type(exc).__name__, backoff,
                )
                time.sleep(backoff)
                last_exc = exc
                continue
            last_exc = exc
            break
    raise last_exc


def gemini_generate(client, primary_model: str, contents: str, fallback_model: str = None) -> str:
    """
    Wrapper around client.models.generate_content() with:
      - rate limiting
      - retry with exponential backoff (429 / network errors)
      - immediate fallback on 503 (no wasted retries on overloaded model)

    Returns response.text (stripped).
    """
    try:
        return _try_model(client, primary_model, contents)
    except Exception as exc:
        if fallback_model and _is_503(exc):
            logger.warning(
                "Primary model %s returned 503. Switching immediately to fallback %s.",
                primary_model, fallback_model,
            )
            return _try_model(client, fallback_model, contents)
        raise
