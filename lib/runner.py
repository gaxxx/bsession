"""Skill runner — executes skill steps against a Toolset.

Walks through a skill's steps, resolves {{variables}} from config + step
outputs, calls the appropriate tool for each action, and builds up a
context dict that tracks all state.

Also handles the monitor loop (repeated execution with change detection
and notification).
"""

from __future__ import annotations

import os
import re
import time
from typing import Callable

from lib.skill import (
    Skill, SkillResult, SkillStep,
    resolve_vars, resolve_dict,
)
from lib.toolset import Toolset


# ── Step executors ───────────────────────────────────────────────────

def _step_navigate(tools: Toolset, params: dict, ctx: dict) -> None:
    url = resolve_vars(params["url"], ctx)
    wait = float(params.get("wait", 3))
    tools.log(f"Navigating to {url}")
    tools.browser.navigate(url, wait=wait)


def _step_bypass(tools: Toolset, params: dict, ctx: dict) -> None:
    bypass_type = params.get("type", "cloudflare")
    if bypass_type == "cloudflare":
        snap = tools.browser.snapshot()
        if tools.bypass.is_cloudflare(snap):
            tools.log("Cloudflare detected, attempting bypass...")
            max_wait = int(params.get("max_wait", 300))
            ok = tools.bypass.cloudflare(snap, max_wait=max_wait)
            if not ok:
                raise RuntimeError("Cloudflare bypass failed")
            tools.log("Cloudflare resolved.")
        # Check if blocked after bypass
        snap = tools.browser.snapshot()
        if tools.bypass.is_blocked(snap):
            raise RuntimeError("IP blocked by protection service")


def _step_find(tools: Toolset, params: dict, ctx: dict) -> None:
    """Find an element by trying multiple patterns. Stores ref in context."""
    name = params["name"]
    patterns = [resolve_vars(p, ctx) for p in params["patterns"]]

    # Take a fresh snapshot if not explicitly provided
    snap = ctx.get("_snapshot") or tools.browser.snapshot()
    ctx["_snapshot"] = snap

    ref = tools.browser.find_first(snap, patterns)
    if not ref:
        if params.get("optional"):
            ctx[name] = ""
            return
        raise RuntimeError(
            f"Could not find element '{name}' with patterns {patterns}"
        )
    ctx[name] = ref
    tools.log(f"Found '{name}' → ref={ref}")


def _step_fill(tools: Toolset, params: dict, ctx: dict) -> None:
    ref = resolve_vars(params["ref"], ctx)
    value = resolve_vars(params["value"], ctx)
    tools.browser.clear(ref)
    tools.browser.fill(ref, value)
    tools.log(f"Filled ref={ref}")


def _step_clear(tools: Toolset, params: dict, ctx: dict) -> None:
    ref = resolve_vars(params["ref"], ctx)
    tools.browser.clear(ref)


def _step_click(tools: Toolset, params: dict, ctx: dict) -> None:
    ref = resolve_vars(params["ref"], ctx)
    wait = float(params.get("wait", 1))
    tools.browser.click(ref)
    tools.log(f"Clicked ref={ref}")
    if wait > 0:
        time.sleep(wait)


def _step_type(tools: Toolset, params: dict, ctx: dict) -> None:
    ref = resolve_vars(params["ref"], ctx)
    value = resolve_vars(params["value"], ctx)
    tools.browser.type_text(ref, value)


def _step_extract(tools: Toolset, params: dict, ctx: dict) -> None:
    """Extract text from the current snapshot using regex."""
    name = params["name"]
    pattern = resolve_vars(params["pattern"], ctx)
    exclude = params.get("exclude", "")
    max_lines = int(params.get("max_lines", 1))

    snap = ctx.get("_snapshot") or tools.browser.snapshot()
    ctx["_snapshot"] = snap

    matches = re.findall(pattern, snap)
    if exclude:
        matches = [m for m in matches if exclude not in m]

    result = "\n".join(matches[:max_lines]) if matches else ""
    ctx[name] = result
    tools.log(f"Extracted '{name}' = {result[:80]}{'...' if len(result) > 80 else ''}")


def _step_wait(tools: Toolset, params: dict, ctx: dict) -> None:
    seconds = float(resolve_vars(str(params.get("seconds", 1)), ctx))
    time.sleep(seconds)


def _step_snapshot(tools: Toolset, params: dict, ctx: dict) -> None:
    """Take a fresh snapshot and store it in context."""
    name = params.get("name", "_snapshot")
    snap = tools.browser.snapshot()
    ctx[name] = snap
    ctx["_snapshot"] = snap


def _step_check(tools: Toolset, params: dict, ctx: dict) -> None:
    """Assert a condition on the current snapshot or context."""
    snap = ctx.get("_snapshot") or tools.browser.snapshot()
    pattern = params.get("snapshot_pattern", "")
    if pattern:
        if not re.search(resolve_vars(pattern, ctx), snap, re.IGNORECASE):
            error = resolve_vars(params.get("error", f"Check failed: {pattern}"), ctx)
            raise RuntimeError(error)


def _step_select(tools: Toolset, params: dict, ctx: dict) -> None:
    """Select a dropdown/combobox option by visible text.

    Uses agent-browser's native select command.
    skip_if_empty: if ref or value resolves to empty, skip this step.
    """
    ref = resolve_vars(params.get("ref", ""), ctx)
    value = resolve_vars(params.get("value", ""), ctx)

    if params.get("skip_if_empty") and (not ref or not value):
        return

    if not ref:
        raise RuntimeError("select: ref is empty")

    tools.browser.select(ref, value)
    tools.log(f"Selected '{value}' from dropdown ref={ref}")


def _step_wait_for(tools: Toolset, params: dict, ctx: dict) -> None:
    """Poll snapshot until a pattern appears or timeout.

    Useful for SPAs and dynamic content that loads asynchronously.
    """
    pattern = resolve_vars(params.get("pattern", ""), ctx)
    timeout = float(params.get("timeout", 30))
    interval = float(params.get("interval", 2))

    if not pattern:
        raise RuntimeError("wait_for: pattern is required")

    start = time.monotonic()
    while time.monotonic() - start < timeout:
        snap = tools.browser.snapshot()
        ctx["_snapshot"] = snap
        if re.search(pattern, snap, re.IGNORECASE):
            tools.log(f"wait_for: found '{pattern}'")
            return
        time.sleep(interval)

    raise RuntimeError(f"wait_for: '{pattern}' not found within {timeout}s")


def _step_captcha(tools: Toolset, params: dict, ctx: dict) -> None:
    """Handle CAPTCHA with human-in-the-loop.

    1. Takes a screenshot and saves it
    2. Sends webhook notification (if configured) with screenshot path
    3. Waits for human to provide the answer via:
       - A file at a known path (poll-based)
       - Or manual VNC solve + the answer appears in the page
    4. Fills the CAPTCHA input with the answer

    Params:
        ref: the CAPTCHA input field ref (from find step)
        screenshot_path: where to save the screenshot (default: /workspace/data/captcha.png)
        answer_file: path to poll for human answer (default: /workspace/data/captcha-answer.txt)
        notify_url: webhook to notify human (optional)
        timeout: max seconds to wait for answer (default: 300)
        poll_interval: seconds between polls (default: 5)
    """
    ref = resolve_vars(params.get("ref", ""), ctx)
    screenshot_path = params.get("screenshot_path", "/workspace/data/captcha.png")
    answer_file = params.get("answer_file", "/workspace/data/captcha-answer.txt")
    notify_url = resolve_vars(params.get("notify_url", ""), ctx)
    timeout = float(params.get("timeout", 300))
    poll_interval = float(params.get("poll_interval", 5))

    # 1. Take screenshot
    tools.browser.screenshot(screenshot_path)
    tools.log(f"CAPTCHA screenshot saved: {screenshot_path}")
    tools.log("Waiting for human to solve CAPTCHA...")
    tools.log(f"  → View page: http://localhost:6080/vnc.html")
    tools.log(f"  → Or write answer to: {answer_file}")

    # 2. Notify if webhook configured
    if notify_url:
        from lib.notify import send_webhook
        send_webhook(notify_url, {
            "type": "captcha",
            "message": "CAPTCHA needs solving",
            "screenshot": screenshot_path,
            "answer_file": answer_file,
        })

    # 3. Clean up any stale answer file
    import os
    if os.path.exists(answer_file):
        os.remove(answer_file)

    # 4. Poll for answer
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        # Check if human wrote answer to file
        if os.path.isfile(answer_file):
            with open(answer_file) as f:
                answer = f.read().strip()
            if answer:
                tools.log(f"CAPTCHA answer received: {answer}")
                if ref:
                    tools.browser.fill(ref, answer)
                ctx["_captcha_answer"] = answer
                os.remove(answer_file)
                return

        time.sleep(poll_interval)

    raise RuntimeError(f"CAPTCHA not solved within {timeout}s")


def _step_transform(tools: Toolset, params: dict, ctx: dict) -> None:
    """Transform a context variable using simple string operations.

    Operations: strip, replace, regex_extract, lower, upper
    Stores result back into context under 'name'.
    """
    source = resolve_vars(params.get("source", ""), ctx)
    name = params["name"]
    operation = params.get("operation", "strip")

    match operation:
        case "strip":
            chars = params.get("chars", " \t\n")
            ctx[name] = source.strip(chars)
        case "replace":
            old = params.get("old", "")
            new = params.get("new", "")
            ctx[name] = source.replace(old, new)
        case "regex_extract":
            regex = params.get("regex", "")
            m = re.search(regex, source)
            ctx[name] = m.group(1) if m and m.groups() else (m.group(0) if m else "")
        case "lower":
            ctx[name] = source.lower()
        case "upper":
            ctx[name] = source.upper()
        case "strip_chars":
            chars = params.get("chars", ",$")
            ctx[name] = "".join(c for c in source if c not in chars)
        case _:
            ctx[name] = source

    tools.log(f"Transformed '{name}' = {ctx[name][:60]}")


STEP_HANDLERS: dict[str, Callable] = {
    "navigate": _step_navigate,
    "bypass": _step_bypass,
    "find": _step_find,
    "fill": _step_fill,
    "clear": _step_clear,
    "click": _step_click,
    "type": _step_type,
    "extract": _step_extract,
    "wait": _step_wait,
    "snapshot": _step_snapshot,
    "check": _step_check,
    "select": _step_select,
    "captcha": _step_captcha,
    "wait_for": _step_wait_for,
    "transform": _step_transform,
}


# ── Single execution ─────────────────────────────────────────────────

def run_skill_once(
    skill: Skill,
    tools: Toolset,
    config: dict[str, str],
) -> SkillResult:
    """Execute a skill's steps once. Returns SkillResult."""
    start = time.monotonic()

    # Build initial context from config + defaults
    ctx: dict[str, str] = {}
    for p in skill.params:
        val = config.get(p.name, p.default or "")
        if p.required and not val:
            return SkillResult(
                success=False,
                error=f"Missing required param: {p.name}",
                duration_ms=0,
            )
        ctx[p.name] = val

    # Add well-known context vars
    ctx["SESSION_NAME"] = config.get("SESSION_NAME", skill.name)

    try:
        for i, step in enumerate(skill.steps):
            handler = STEP_HANDLERS.get(step.action)
            if not handler:
                raise RuntimeError(f"Unknown step action: {step.action}")
            handler(tools, step.params, ctx)
            # Clear snapshot cache between steps that change the page
            if step.action in ("navigate", "click", "fill", "type", "select"):
                ctx.pop("_snapshot", None)

        # Build result from template
        data = resolve_dict(skill.result_template, ctx)

        duration = int((time.monotonic() - start) * 1000)
        return SkillResult(success=True, data=data, duration_ms=duration)

    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        tools.log(f"Skill failed: {e}")
        return SkillResult(success=False, error=str(e), duration_ms=duration)


# ── Monitor loop ─────────────────────────────────────────────────────

def run_skill_monitor(
    skill: Skill,
    tools: Toolset,
    config: dict[str, str],
    eval_recorder=None,
) -> None:
    """Run a skill in a monitor loop: execute → diff → notify → sleep → repeat.

    Blocks forever (or until max_failures exceeded). Designed to run in
    a subprocess launched by session.py.
    """
    mon = skill.monitor
    if not mon:
        # Single execution, not a monitor
        result = run_skill_once(skill, tools, config)
        if eval_recorder:
            eval_recorder.record(skill.name, config.get("SESSION_NAME", skill.name), result)
        return

    interval = int(resolve_vars(mon.interval, config))
    change_field = mon.change_field
    webhook_url = resolve_vars(mon.notify_webhook, config) if mon.notify_webhook else ""

    # Load previous state
    session_name = config.get("SESSION_NAME", skill.name)
    state_file = f"/workspace/data/{skill.name}-{session_name}-last.txt"
    previous_value = ""
    if os.path.isfile(state_file):
        previous_value = open(state_file).read().strip()
        tools.log(f"Previous {change_field}: {previous_value}")

    consecutive_failures = 0
    check_count = 0

    while True:
        check_count += 1
        tools.log(f"Check #{check_count} starting...")

        result = run_skill_once(skill, tools, config)
        if eval_recorder:
            eval_recorder.record(skill.name, session_name, result)

        if not result.success:
            consecutive_failures += 1
            retry_delay = interval * consecutive_failures
            tools.log(f"Failed (#{consecutive_failures}): {result.error}")
            if consecutive_failures >= mon.max_failures:
                tools.log("Too many failures. Exiting.")
                return
            tools.log(f"Retrying in {retry_delay // 60} min.")
            time.sleep(retry_delay)
            continue

        consecutive_failures = 0
        current_value = result.data.get(change_field, "")
        tools.log(f"{change_field}: {current_value}")

        # Save state
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w") as f:
            f.write(current_value)

        # Detect change
        if previous_value and current_value != previous_value and webhook_url:
            tools.log(f"*** CHANGED *** {previous_value} → {current_value}")
            payload_ctx = {
                **config,
                **result.data,
                "_previous": previous_value,
                "_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            # Resolve notify payload template
            payload = resolve_dict(mon.notify_payload, payload_ctx) if mon.notify_payload else result.data
            ok = tools.notify.webhook(webhook_url, payload)
            tools.log("Notification sent." if ok else "WARNING: Notification failed.")

            # Append to history
            history_file = f"/workspace/data/{skill.name}-{session_name}-history.txt"
            with open(history_file, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}|{current_value}\n")
        elif not previous_value:
            tools.log("First run, baseline recorded.")
        else:
            tools.log("Unchanged.")

        previous_value = current_value
        time.sleep(interval)
