"""Toolset — typed wrappers around browser, bypass, notify, chrome, screenshot.

A Toolset is bound to a CDP port at construction time. Skills call
tools.browser.navigate(url) etc. without knowing about ports.

Each tool group is defined as a Protocol for testability — tests can
substitute mock implementations without touching production code.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from lib.browser import (
    ab, ab_quiet, find_ref, find_all_refs, capture_screenshot, make_logger,
)
from lib.chrome import start_chrome, stop_chrome, chrome_alive
from lib.bypass.cloudflare import is_cloudflare, wait_for_cloudflare
from lib.notify import send_webhook
from lib.captcha import find_captcha_bounds, capture_captcha_screenshot


# ── Protocols (for mocking in tests) ─────────────────────────────────

class BrowserTools(Protocol):
    def navigate(self, url: str, wait: float = 3) -> None: ...
    def snapshot(self) -> str: ...
    def click(self, ref: str) -> None: ...
    def fill(self, ref: str, value: str) -> None: ...
    def clear(self, ref: str) -> None: ...
    def type_text(self, ref: str, text: str) -> None: ...
    def find_ref(self, snapshot: str, pattern: str) -> str | None: ...
    def find_all_refs(self, snapshot: str, pattern: str) -> list[str]: ...
    def find_first(self, snapshot: str, patterns: list[str]) -> str | None: ...


class BypassTools(Protocol):
    def is_cloudflare(self, snapshot: str) -> bool: ...
    def cloudflare(self, snapshot: str, max_wait: int = 300) -> bool: ...
    def is_blocked(self, snapshot: str) -> bool: ...


class NotifyTools(Protocol):
    def webhook(self, url: str, payload: dict) -> bool: ...


class ChromeTools(Protocol):
    def alive(self) -> bool: ...
    def start(self, profile_dir: str, url: str = "about:blank") -> int: ...
    def stop(self) -> None: ...


class ScreenshotTools(Protocol):
    def capture(self) -> bytes: ...


class CaptchaTools(Protocol):
    def bounds(self) -> dict | None: ...
    def screenshot(self, padding: int = 10) -> bytes: ...


# ── Concrete implementations ─────────────────────────────────────────

@dataclass(frozen=True)
class _BrowserTools:
    port: int

    def navigate(self, url: str, wait: float = 3) -> None:
        ab_quiet(self.port, "open", url)
        if wait > 0:
            time.sleep(wait)

    def snapshot(self) -> str:
        return ab(self.port, "snapshot")

    def click(self, ref: str) -> None:
        ab_quiet(self.port, "click", ref)

    def fill(self, ref: str, value: str) -> None:
        ab_quiet(self.port, "fill", ref, value)

    def clear(self, ref: str) -> None:
        ab_quiet(self.port, "clear", ref)

    def type_text(self, ref: str, text: str) -> None:
        ab_quiet(self.port, "type", ref, text)

    def select(self, ref: str, value: str) -> None:
        """Select a dropdown option using agent-browser's native select."""
        ab_quiet(self.port, "select", ref, value)

    def screenshot(self, path: str = "/tmp/bsession-capture.png") -> str:
        """Save a screenshot using agent-browser. Returns the file path."""
        ab_quiet(self.port, "screenshot", path)
        return path

    def find_ref(self, snapshot: str, pattern: str) -> str | None:
        return find_ref(snapshot, pattern)

    def find_all_refs(self, snapshot: str, pattern: str) -> list[str]:
        return find_all_refs(snapshot, pattern)

    def find_first(self, snapshot: str, patterns: list[str]) -> str | None:
        """Try multiple patterns, return first match."""
        for p in patterns:
            ref = find_ref(snapshot, p)
            if ref:
                return ref
        return None


@dataclass(frozen=True)
class _BypassTools:
    port: int
    log: Callable | None = None

    def is_cloudflare(self, snapshot: str) -> bool:
        return is_cloudflare(snapshot)

    def cloudflare(self, snapshot: str, max_wait: int = 300) -> bool:
        return wait_for_cloudflare(self.port, snapshot, max_wait=max_wait, log=self.log)

    def is_blocked(self, snapshot: str) -> bool:
        return bool(re.search(
            r"you have been blocked|unable to access", snapshot, re.IGNORECASE,
        ))


@dataclass(frozen=True)
class _NotifyTools:
    def webhook(self, url: str, payload: dict) -> bool:
        return send_webhook(url, payload)


@dataclass(frozen=True)
class _ChromeTools:
    port: int

    def alive(self) -> bool:
        return chrome_alive(self.port)

    def start(self, profile_dir: str, url: str = "about:blank") -> int:
        return start_chrome(self.port, profile_dir, url)

    def stop(self) -> None:
        stop_chrome(self.port)


@dataclass(frozen=True)
class _ScreenshotTools:
    port: int

    def capture(self) -> bytes:
        return capture_screenshot(self.port)


@dataclass(frozen=True)
class _CaptchaTools:
    port: int

    def bounds(self) -> dict | None:
        """Return {x, y, width, height} of the captcha element, or None."""
        return find_captcha_bounds(self.port)

    def screenshot(self, padding: int = 10) -> bytes:
        """Capture a PNG screenshot of just the captcha area."""
        return capture_captcha_screenshot(self.port, padding=padding)


# ── Toolset facade ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Toolset:
    """All tools available to a skill, bound to a single CDP port."""
    browser: _BrowserTools
    bypass: _BypassTools
    notify: _NotifyTools
    chrome: _ChromeTools
    screenshot: _ScreenshotTools
    captcha: _CaptchaTools
    log: Callable = field(default=print)


def create_toolset(
    cdp_port: int,
    log: Callable | None = None,
    session_name: str = "skill",
) -> Toolset:
    """Factory: build a Toolset wired to a CDP port."""
    log_fn = log or make_logger(session_name)
    return Toolset(
        browser=_BrowserTools(port=cdp_port),
        bypass=_BypassTools(port=cdp_port, log=log_fn),
        notify=_NotifyTools(),
        chrome=_ChromeTools(port=cdp_port),
        screenshot=_ScreenshotTools(port=cdp_port),
        captcha=_CaptchaTools(port=cdp_port),
        log=log_fn,
    )
