"""Instruction execution engine — parse and run capability instructions.

Parses Markdown instructions into structured tasks, resolves data templates
and element patterns, and drives browser actions via lib/browser.py.

Instruction format:
    # Title
    ## task_name
    1. action <pattern> {data.x.y} -w N
    2. ...

Step syntax:
    - <pat1|pat2>  — element search patterns (pipe-separated, tried in order)
    - {data.x.y}   — value from TOML data file (section.key)
    - {var.name}    — reference to previously extracted variable
    - -w N          — wait N seconds after action
"""

import os
import re
import time
from dataclasses import dataclass, field

from lib.browser import (
    ab, ab_quiet, find_ref, find_all_refs,
    is_cloudflare, wait_for_cloudflare,
    capture_screenshot, make_logger,
)


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class Step:
    line_num: int
    action: str
    args: list[str]
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class Task:
    name: str
    steps: list[Step] = field(default_factory=list)


@dataclass
class Instructions:
    title: str
    tasks: dict[str, Task] = field(default_factory=dict)
    default_task: str | None = None


# ── Parsing ──────────────────────────────────────────────────────────

_STEP_RE = re.compile(r'^\d+\.\s+(.+)$')
_OPTION_RE = re.compile(r'-(\w)\s+(\S+)')


def parse_instructions(text: str) -> Instructions:
    """Parse markdown instructions into structured Instructions."""
    instructions = Instructions(title="")
    current_task = None

    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()

        # Title
        if stripped.startswith("# ") and not stripped.startswith("## "):
            instructions.title = stripped[2:].strip()
            continue

        # Task section
        if stripped.startswith("## "):
            task_name = stripped[3:].strip()
            current_task = Task(name=task_name)
            instructions.tasks[task_name] = current_task
            if instructions.default_task is None:
                instructions.default_task = task_name
            continue

        # Step line
        m = _STEP_RE.match(stripped)
        if m and current_task is not None:
            step = _parse_step(i, m.group(1))
            current_task.steps.append(step)

    return instructions


def _parse_step(line_num: int, text: str) -> Step:
    """Parse a single step line into a Step object."""
    # Extract -w N and other flags before parsing action
    options = {}
    for m in _OPTION_RE.finditer(text):
        options[m.group(1)] = m.group(2)
    # Remove options from text
    cleaned = _OPTION_RE.sub('', text).strip()

    parts = cleaned.split(None, 1)
    action = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    # Split remaining into args (preserving <...> and {...} as single tokens)
    args = _split_args(rest)

    return Step(line_num=line_num, action=action, args=args, options=options)


def _split_args(text: str) -> list[str]:
    """Split step arguments, keeping <...> and {...} as single tokens."""
    args = []
    i = 0
    current = []

    while i < len(text):
        ch = text[i]
        if ch in ('<', '{'):
            # Flush any accumulated text
            if current:
                for part in ''.join(current).split():
                    if part:
                        args.append(part)
                current = []
            # Find matching close
            close = '>' if ch == '<' else '}'
            j = text.index(close, i) + 1 if close in text[i:] else len(text)
            args.append(text[i:j])
            i = j
        else:
            current.append(ch)
            i += 1

    if current:
        for part in ''.join(current).split():
            if part:
                args.append(part)

    return args


# ── Data loading ─────────────────────────────────────────────────────

def load_data(toml_path: str) -> dict[str, str]:
    """Load TOML file and flatten to dotted keys: {section.key: value}."""
    import tomllib
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    flat = {}
    for section, values in data.items():
        if isinstance(values, dict):
            for key, val in values.items():
                flat[f"{section}.{key}"] = str(val)
        else:
            flat[section] = str(values)
    return flat


# ── Value & ref resolution ───────────────────────────────────────────

_TEMPLATE_RE = re.compile(r'\{(data|var)\.([^}]+)\}')


def resolve_value(template: str, data: dict[str, str], variables: dict[str, str]) -> str:
    """Replace {data.x.y} and {var.name} in template string."""
    def _replace(m):
        kind, key = m.group(1), m.group(2)
        if kind == "data":
            return data.get(key, m.group(0))
        return variables.get(key, m.group(0))
    return _TEMPLATE_RE.sub(_replace, template)


def resolve_ref(snapshot: str, patterns_str: str) -> str:
    """Try pipe-separated patterns against snapshot, return first matching ref."""
    # Strip angle brackets
    inner = patterns_str.strip('<>') if patterns_str.startswith('<') else patterns_str
    patterns = [p.strip() for p in inner.split('|')]

    for pattern in patterns:
        ref = find_ref(snapshot, pattern)
        if ref:
            return ref

    raise ValueError(f"No element found matching: {patterns_str}")


# ── Task execution ───────────────────────────────────────────────────

def execute_task(port: int, task: Task, data: dict[str, str], log) -> dict[str, str]:
    """Execute a parsed task. Returns extracted variables."""
    variables = {}
    snapshot = None  # lazy-loaded

    def get_snapshot():
        nonlocal snapshot
        if snapshot is None:
            snapshot = ab(port, "snapshot")
        return snapshot

    def refresh_snapshot():
        nonlocal snapshot
        snapshot = ab(port, "snapshot")
        return snapshot

    def invalidate_snapshot():
        nonlocal snapshot
        snapshot = None

    for step in task.steps:
        action = step.action
        wait_secs = int(step.options.get('w', 0))
        log(f"Step {step.line_num}: {action} {' '.join(step.args)}")

        try:
            if action == "navigate":
                url = resolve_value(step.args[0], data, variables) if step.args else ""
                ab_quiet(port, "open", url)
                if wait_secs:
                    time.sleep(wait_secs)
                refresh_snapshot()

            elif action == "bypass":
                snap = get_snapshot()
                if is_cloudflare(snap):
                    log("  Cloudflare detected, attempting bypass...")
                    ok = wait_for_cloudflare(port, snap)
                    if ok:
                        refresh_snapshot()
                    else:
                        log("  Bypass failed — solve at http://localhost:6080/vnc.html")
                else:
                    log("  No Cloudflare detected, skipping.")

            elif action == "fill":
                patterns = step.args[0] if step.args else ""
                value = resolve_value(step.args[1], data, variables) if len(step.args) > 1 else ""
                ref = resolve_ref(get_snapshot(), patterns)
                ab_quiet(port, "clear", ref)
                ab_quiet(port, "fill", ref, value)
                log(f"  Filled ref={ref}")

            elif action == "click":
                patterns = step.args[0] if step.args else ""
                ref = resolve_ref(get_snapshot(), patterns)
                ab_quiet(port, "click", ref)
                if wait_secs:
                    time.sleep(wait_secs)
                refresh_snapshot()
                log(f"  Clicked ref={ref}")

            elif action == "select":
                patterns = step.args[0] if step.args else ""
                value = resolve_value(step.args[1], data, variables) if len(step.args) > 1 else ""
                ref = resolve_ref(get_snapshot(), patterns)
                ab_quiet(port, "click", ref)
                time.sleep(1)
                snap = refresh_snapshot()
                option_ref = find_ref(snap, re.escape(value))
                if option_ref:
                    ab_quiet(port, "click", option_ref)
                    if wait_secs:
                        time.sleep(wait_secs)
                    refresh_snapshot()
                    log(f"  Selected '{value}'")
                else:
                    log(f"  Option '{value}' not found")

            elif action == "type":
                patterns = step.args[0] if step.args else ""
                value = resolve_value(step.args[1], data, variables) if len(step.args) > 1 else ""
                ref = resolve_ref(get_snapshot(), patterns)
                ab_quiet(port, "type", ref, value)
                log(f"  Typed into ref={ref}")

            elif action == "clear":
                patterns = step.args[0] if step.args else ""
                ref = resolve_ref(get_snapshot(), patterns)
                ab_quiet(port, "clear", ref)
                log(f"  Cleared ref={ref}")

            elif action == "extract":
                var_name = step.args[0] if step.args else "unnamed"
                pattern = step.args[1].strip('<>') if len(step.args) > 1 else ""
                snap = get_snapshot()
                match = re.search(pattern, snap, re.IGNORECASE)
                value = match.group(1) if match and match.lastindex else (match.group(0) if match else "")
                variables[var_name] = value
                log(f"  Extracted {var_name}={value!r}")

            elif action == "wait":
                secs = int(step.args[0]) if step.args else (wait_secs or 3)
                time.sleep(secs)
                invalidate_snapshot()
                log(f"  Waited {secs}s")

            elif action == "screenshot":
                png = capture_screenshot(port)
                out_path = f"/tmp/bsession-step-{step.line_num}.png"
                with open(out_path, "wb") as f:
                    f.write(png)
                log(f"  Screenshot saved: {out_path}")

            elif action == "snapshot":
                refresh_snapshot()

            elif action == "js":
                element_id = step.args[0] if step.args else ""
                value = resolve_value(step.args[1], data, variables) if len(step.args) > 1 else ""
                js_code = (
                    f"var s=document.getElementById('{element_id}');"
                    f"s.value='{value}';"
                    "s.dispatchEvent(new Event('change',{bubbles:true}));"
                )
                ab(port, "eval", js_code)
                if wait_secs:
                    time.sleep(wait_secs)
                log(f"  JS set #{element_id}={value!r}")

            elif action == "click_all_no":
                snap = get_snapshot()
                unchecked = []
                for line in snap.splitlines():
                    if re.search(r'radio "No"', line, re.IGNORECASE) and 'checked' not in line.lower():
                        m = re.search(r'\[ref=(\w+)\]', line)
                        if m:
                            unchecked.append(m.group(1))
                for ref in unchecked:
                    ab_quiet(port, "click", ref)
                if unchecked:
                    refresh_snapshot()
                log(f"  Clicked {len(unchecked)} 'No' radio buttons")

            else:
                log(f"  Unknown action: {action}")

        except Exception as e:
            log(f"  ERROR at step {step.line_num}: {e}")
            raise

    return variables


# ── Convenience entry point ──────────────────────────────────────────

def run(session_id: str, task_name: str | None, data_path: str | None,
        conf: dict, port: int) -> dict[str, str]:
    """Load instructions, parse, determine task, load data, execute."""
    # Load instructions
    inst_file = conf.get("instructions")
    if not inst_file:
        raise RuntimeError(f"Session '{session_id}' has no instructions file")

    inst_path = os.path.join("/workspace", inst_file)
    if not os.path.isfile(inst_path):
        raise RuntimeError(f"Instructions file not found: {inst_path}")

    with open(inst_path) as f:
        instructions = parse_instructions(f.read())

    # Determine task
    name = task_name or instructions.default_task
    if not name or name not in instructions.tasks:
        available = ', '.join(instructions.tasks.keys())
        raise RuntimeError(f"Task '{name}' not found. Available: {available}")
    task = instructions.tasks[name]

    # Load data
    if not data_path:
        data_path = conf.get("data")
    data = {}
    if data_path:
        full_data_path = os.path.join("/workspace", data_path)
        if os.path.isfile(full_data_path):
            data = load_data(full_data_path)

    # Execute
    log = make_logger(session_id)
    log(f"Running task '{name}' ({len(task.steps)} steps)")
    return execute_task(port, task, data, log)
