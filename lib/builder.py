"""Skill builder — create, validate, and export skills.

The 5-step builder workflow:
  1. Name & describe the task
  2. Define steps (navigate, find, fill, click, extract, etc.)
  3. Run and query the result
  4. Export as Claude / OpenClaw skill
  5. Update tools and description based on eval

This module handles steps 1, 2, 4, 5 programmatically.
Steps 3 is handled by runner.py + eval.py.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from lib.skill import (
    Skill, SkillParam, SkillStep, MonitorConfig,
    SKILLS_DIR, load_skill, list_skills,
)

# PyYAML optional
try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# ── Step 1: Scaffold ─────────────────────────────────────────────────

SKILL_TEMPLATE = """\
# {name} — bsession skill
# Created: {created}
# Docs: https://github.com/gaxxx/bsession#skills

name: {name}
description: "{description}"
version: "1.0.0"

# Tags help with discovery
tags:
  - monitor

# Parameters the skill needs (from conf [env] section)
params:
  - name: EXAMPLE_PARAM
    description: "Replace with your parameter"
    required: true
#  - name: CHECK_INTERVAL
#    description: "Seconds between checks"
#    required: false
#    default: "3600"

# Steps define the automation flow
# Available actions: navigate, bypass, find, fill, clear, click,
#                    type, select, extract, wait, wait_for, transform,
#                    snapshot, check
steps:
  - action: navigate
    url: "https://example.com"
    wait: 5

  - action: bypass
    type: cloudflare

  - action: snapshot
    name: page

  # Find an element by trying patterns in order
  # - action: find
  #   name: my_input
  #   patterns:
  #     - textbox
  #     - "input.*name"

  # Fill a form field
  # - action: fill
  #   ref: "{{{{my_input}}}}"
  #   value: "{{{{EXAMPLE_PARAM}}}}"

  # Click a button
  # - action: click
  #   ref: "{{{{submit_button}}}}"
  #   wait: 5

  # Extract text from the page
  - action: extract
    name: page_title
    pattern: 'heading "([^"]*)"'
    max_lines: 1

# What the skill outputs after execution
result:
  title: "{{{{page_title}}}}"

# Optional: run as a recurring monitor
# monitor:
#   interval: "{{{{CHECK_INTERVAL}}}}"
#   change_field: title
#   max_failures: 5
#   notify:
#     webhook: "{{{{N8N_WEBHOOK_URL}}}}"
#     payload:
#       session: "{{{{SESSION_NAME}}}}"
#       new_status: "{{{{page_title}}}}"
"""


def scaffold_skill(name: str, description: str = "") -> str:
    """Generate a skill YAML file and return the path.

    Step 1 of the builder workflow.
    """
    os.makedirs(SKILLS_DIR, exist_ok=True)
    path = os.path.join(SKILLS_DIR, f"{name}.yaml")
    if os.path.exists(path):
        raise FileExistsError(f"Skill already exists: {path}")

    content = SKILL_TEMPLATE.format(
        name=name,
        description=description or f"{name} automation skill",
        created=time.strftime("%Y-%m-%d"),
    )
    with open(path, "w") as f:
        f.write(content)
    return path


# ── Step 2: Build steps programmatically ─────────────────────────────

def add_step_to_skill(
    skill_path: str,
    action: str,
    params: dict[str, Any],
) -> None:
    """Append a step to an existing skill YAML file.

    Step 2 of the builder workflow — called iteratively to build up steps.
    """
    raw = _read_yaml(skill_path)
    step = {"action": action, **params}
    raw.setdefault("steps", []).append(step)
    _write_yaml(skill_path, raw)


def set_skill_params(
    skill_path: str,
    params: list[dict[str, Any]],
) -> None:
    """Set the params list for a skill."""
    raw = _read_yaml(skill_path)
    raw["params"] = params
    _write_yaml(skill_path, raw)


def set_skill_result(
    skill_path: str,
    result_template: dict[str, str],
) -> None:
    """Set the result template for a skill."""
    raw = _read_yaml(skill_path)
    raw["result"] = result_template
    _write_yaml(skill_path, raw)


def set_skill_monitor(
    skill_path: str,
    interval: str = "3600",
    change_field: str = "",
    webhook: str = "",
    payload: dict | None = None,
) -> None:
    """Configure monitor mode for a skill."""
    raw = _read_yaml(skill_path)
    monitor: dict[str, Any] = {
        "interval": interval,
        "change_field": change_field,
        "max_failures": 5,
    }
    if webhook:
        monitor["notify"] = {"webhook": webhook}
        if payload:
            monitor["notify"]["payload"] = payload
    raw["monitor"] = monitor
    _write_yaml(skill_path, raw)


# ── Step 4: Export to Claude / OpenClaw ──────────────────────────────

def export_claude_skill(skill: Skill) -> str:
    """Export a skill as a Claude Code skill definition (Markdown).

    Returns markdown content suitable for saving as a .md file in
    ~/.claude/skills/ or .claude/skills/.
    """
    param_lines = []
    for p in skill.params:
        req = "(required)" if p.required else f"(optional, default: {p.default})"
        param_lines.append(f"  - `{p.name}` — {p.description} {req}")

    step_lines = []
    for i, s in enumerate(skill.steps, 1):
        desc = _describe_step(s)
        step_lines.append(f"  {i}. {desc}")

    result_fields = ", ".join(f"`{k}`" for k in skill.result_template)

    tools_section = ""
    if skill.tools_used:
        tools_section = f"\n## Tools Used\n\n{', '.join(skill.tools_used)}\n"

    return f"""\
# {skill.name}

> {skill.description}

**Version:** {skill.version}

## Parameters

{chr(10).join(param_lines) if param_lines else "  (none)"}

## Steps

{chr(10).join(step_lines)}

## Result

Returns: {result_fields or "(none)"}

{"## Monitor" if skill.monitor else ""}
{"Runs every " + skill.monitor.interval + "s, detects changes in `" + skill.monitor.change_field + "`." if skill.monitor else ""}
{tools_section}
## Usage

This skill is executed by the bsession browser automation engine.
Configure it via a `.conf` file:

```ini
[session]
skill = {skill.name}

[env]
{chr(10).join(f"{p.name} = <value>" for p in skill.params)}
```

Then run: `bsession skill run {skill.name}`
"""


def export_openclaw_tool(skill: Skill) -> dict:
    """Export a skill as an OpenClaw tool definition (JSON).

    Returns a dict suitable for registering as a tool in OpenClaw.
    """
    properties = {}
    required = []
    for p in skill.params:
        properties[p.name] = {
            "type": "string",
            "description": p.description,
        }
        if p.default:
            properties[p.name]["default"] = p.default
        if p.required:
            required.append(p.name)

    return {
        "name": f"bsession_{skill.name}",
        "description": skill.description,
        "version": skill.version,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
        "metadata": {
            "engine": "bsession",
            "skill": skill.name,
            "tags": skill.tags,
            "monitor": skill.monitor is not None,
        },
    }


# ── Step 5: Update skill based on eval ───────────────────────────────

def update_skill_description(skill_path: str, description: str) -> None:
    """Update a skill's description based on what we learned from runs."""
    raw = _read_yaml(skill_path)
    raw["description"] = description
    _write_yaml(skill_path, raw)


def update_skill_tools_used(skill_path: str, tools: list[str]) -> None:
    """Record which tools this skill actually uses (for export metadata)."""
    raw = _read_yaml(skill_path)
    raw["tools_used"] = tools
    _write_yaml(skill_path, raw)


def bump_skill_version(skill_path: str) -> str:
    """Bump the patch version of a skill. Returns new version."""
    raw = _read_yaml(skill_path)
    parts = raw.get("version", "1.0.0").split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    new_version = ".".join(parts)
    raw["version"] = new_version
    _write_yaml(skill_path, raw)
    return new_version


# ── YAML helpers ─────────────────────────────────────────────────────

def _read_yaml(path: str) -> dict:
    with open(path) as f:
        content = f.read()
    if yaml:
        return yaml.safe_load(content) or {}
    return json.loads(content)


def _write_yaml(path: str, data: dict) -> None:
    with open(path, "w") as f:
        if yaml:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        else:
            json.dump(data, f, indent=2)


def _describe_step(step: SkillStep) -> str:
    """Human-readable description of a step for export."""
    p = step.params
    match step.action:
        case "navigate":
            return f"Navigate to `{p.get('url', '?')}`"
        case "bypass":
            return f"Handle {p.get('type', 'cloudflare')} protection"
        case "find":
            return f"Find element `{p.get('name', '?')}` by patterns"
        case "fill":
            return f"Fill `{p.get('ref', '?')}` with value"
        case "click":
            return f"Click `{p.get('ref', '?')}`"
        case "extract":
            return f"Extract `{p.get('name', '?')}` from page"
        case "wait":
            return f"Wait {p.get('seconds', '?')}s"
        case "snapshot":
            return "Take page snapshot"
        case "check":
            return f"Assert: {p.get('snapshot_pattern', p.get('error', '?'))}"
        case "select":
            return f"Select `{p.get('value', '?')}` from `{p.get('ref', '?')}`"
        case "wait_for":
            return f"Wait for `{p.get('pattern', '?')}` (timeout {p.get('timeout', 30)}s)"
        case "transform":
            return f"Transform `{p.get('source', '?')}` → `{p.get('name', '?')}` ({p.get('operation', 'strip')})"
        case _:
            return f"{step.action}: {p}"
