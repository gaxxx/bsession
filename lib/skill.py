"""Skill data model — YAML-based declarative browser automation skills.

A skill defines:
  - metadata (name, description, version)
  - params (what config it needs)
  - steps (navigate, bypass, find, fill, click, extract, wait, check)
  - result template (what the skill outputs)
  - monitor config (optional: loop interval, change detection, notification)

Skills are loaded from YAML files in workspace/skills/.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import json

# PyYAML may not be installed — fall back to a minimal loader
try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# ── Data classes ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class SkillParam:
    name: str
    description: str = ""
    required: bool = True
    default: str | None = None


@dataclass(frozen=True)
class SkillStep:
    """One step in a skill's execution flow.

    Actions:
      navigate  — open a URL                   {url, wait}
      bypass    — handle protection             {type: cloudflare}
      find      — locate element by patterns    {name, patterns}
      fill      — fill an input field           {ref, value}
      clear     — clear an input field          {ref}
      click     — click an element              {ref, wait}
      type      — type text into element        {ref, value}
      extract   — extract text from snapshot    {name, pattern, exclude, max_lines}
      wait      — sleep                         {seconds}
      check     — assert condition              {condition, snapshot_pattern, error}
      snapshot  — take a snapshot               {name}  (saves to context)
    """
    action: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MonitorConfig:
    """How to run the skill as a recurring monitor."""
    interval: str = "3600"          # seconds between runs (supports {{var}})
    change_field: str = ""          # which result field to diff
    notify_webhook: str = ""        # webhook URL (supports {{var}})
    notify_payload: dict = field(default_factory=dict)
    max_failures: int = 5           # consecutive failures before exit


@dataclass(frozen=True)
class SkillResult:
    """Outcome of a single skill execution."""
    success: bool
    data: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    retries: int = 0
    duration_ms: int = 0


@dataclass(frozen=True)
class Skill:
    """A complete skill definition loaded from YAML."""
    name: str
    description: str
    version: str = "1.0.0"
    params: list[SkillParam] = field(default_factory=list)
    steps: list[SkillStep] = field(default_factory=list)
    result_template: dict[str, str] = field(default_factory=dict)
    monitor: MonitorConfig | None = None

    # ── Tools metadata (for export) ──────────────────────────────────
    tools_used: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


# ── YAML loader ──────────────────────────────────────────────────────

SKILLS_DIR = os.environ.get("SKILLS_DIR", "/workspace/skills")


def _parse_params(raw: list[dict]) -> list[SkillParam]:
    return [
        SkillParam(
            name=p["name"],
            description=p.get("description", ""),
            required=p.get("required", True),
            default=p.get("default"),
        )
        for p in raw
    ]


def _parse_steps(raw: list[dict]) -> list[SkillStep]:
    steps = []
    for s in raw:
        action = s["action"]
        params = {k: v for k, v in s.items() if k != "action"}
        steps.append(SkillStep(action=action, params=params))
    return steps


def _parse_monitor(raw: dict | None) -> MonitorConfig | None:
    if not raw:
        return None
    notify = raw.get("notify", {})
    return MonitorConfig(
        interval=str(raw.get("interval", "3600")),
        change_field=raw.get("change_field", ""),
        notify_webhook=notify.get("webhook", ""),
        notify_payload=notify.get("payload", {}),
        max_failures=raw.get("max_failures", 5),
    )


def _load_yaml(path: str) -> dict:
    """Load YAML file, with fallback to JSON if PyYAML not available."""
    with open(path) as f:
        content = f.read()
    if yaml:
        return yaml.safe_load(content)
    # Fallback: try JSON (skills can be written in JSON too)
    return json.loads(content)


def load_skill(path: str) -> Skill:
    """Load a skill from a YAML file."""
    raw = _load_yaml(path)

    raw_steps = raw.get("steps", [])

    return Skill(
        name=raw["name"],
        description=raw.get("description", ""),
        version=raw.get("version", "1.0.0"),
        params=_parse_params(raw.get("params", [])),
        steps=_parse_steps(raw_steps),
        result_template=raw.get("result", {}),
        monitor=_parse_monitor(raw.get("monitor")),
        tools_used=raw.get("tools_used", []),
        tags=raw.get("tags", []),
    )


def load_skill_by_name(name: str) -> Skill:
    """Load a skill by name from the skills directory."""
    for ext in (".yaml", ".yml", ".json"):
        path = os.path.join(SKILLS_DIR, f"{name}{ext}")
        if os.path.isfile(path):
            return load_skill(path)
    raise FileNotFoundError(f"Skill '{name}' not found in {SKILLS_DIR}")


def list_skills() -> list[dict[str, str]]:
    """List all available skills with basic metadata."""
    skills = []
    if not os.path.isdir(SKILLS_DIR):
        return skills
    for fname in sorted(os.listdir(SKILLS_DIR)):
        if not fname.endswith((".yaml", ".yml", ".json")):
            continue
        try:
            s = load_skill(os.path.join(SKILLS_DIR, fname))
            skills.append({
                "name": s.name,
                "description": s.description,
                "version": s.version,
                "file": fname,
                "params": len(s.params),
                "steps": len(s.steps),
            })
        except Exception as exc:
            import sys
            print(f"Warning: failed to parse skill {fname}: {exc}", file=sys.stderr)
            skills.append({"name": fname, "description": f"(parse error: {exc})", "version": "?"})
    return skills


# ── Variable resolution ──────────────────────────────────────────────

def resolve_vars(template: str, context: dict[str, str]) -> str:
    """Replace {{var}} placeholders with values from context."""
    def _replace(m: re.Match) -> str:
        key = m.group(1).strip()
        return context.get(key, m.group(0))
    return re.sub(r"\{\{(.+?)\}\}", _replace, template)


def resolve_dict(template: dict, context: dict[str, str]) -> dict:
    """Recursively resolve {{var}} in all string values of a dict."""
    result = {}
    for k, v in template.items():
        if isinstance(v, str):
            result[k] = resolve_vars(v, context)
        elif isinstance(v, dict):
            result[k] = resolve_dict(v, context)
        else:
            result[k] = v
    return result
