#!/usr/bin/env python3
"""Subprocess entry point for running a skill.

Launched by session.py when a conf has `skill = ...` instead of `script = ...`.
Handles Chrome lifecycle, toolset creation, eval recording, and the
monitor loop.

Usage:
    python3 /app/run_skill.py <session_id>
"""

import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.skill import load_skill_by_name
from lib.toolset import create_toolset
from lib.runner import run_skill_once, run_skill_monitor
from lib.eval import EvalRecorder
from lib.browser import make_logger


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: run_skill.py <session_id>", file=sys.stderr)
        sys.exit(1)

    session_id = sys.argv[1]

    # Read config from environment (session.py exports [env] vars)
    skill_name = os.environ.get("SKILL_NAME", session_id)
    cdp_port = int(os.environ.get("CDP_PORT", 9222))
    session_name = os.environ.get("SESSION_NAME", session_id)

    log = make_logger(session_name)
    log(f"Loading skill: {skill_name}")

    # Load skill definition
    skill = load_skill_by_name(skill_name)
    log(f"Skill loaded: {skill.name} v{skill.version} ({len(skill.steps)} steps)")

    # Build config dict from environment
    config: dict[str, str] = {}
    for p in skill.params:
        val = os.environ.get(p.name, p.default or "")
        config[p.name] = val
    config["SESSION_NAME"] = session_name
    config["CDP_PORT"] = str(cdp_port)

    # Create toolset + eval recorder
    tools = create_toolset(cdp_port, log=log, session_name=session_name)
    recorder = EvalRecorder()

    # Handle graceful shutdown
    def _shutdown(signum, frame):
        log("Received shutdown signal.")
        sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)

    # Run
    if skill.monitor:
        log(f"Starting monitor loop (interval from config)...")
        run_skill_monitor(skill, tools, config, eval_recorder=recorder)
    else:
        log("Running skill once...")
        result = run_skill_once(skill, tools, config)
        recorder.record(skill.name, session_name, result)
        if result.success:
            log(f"Skill completed: {result.data}")
        else:
            log(f"Skill failed: {result.error}")
            sys.exit(1)


if __name__ == "__main__":
    main()
