"""form.toml resolution and field access.

A form path resolves to (skill_id, form_id, profile, fields):
  .claude/skills/uscis-check/forms/example.toml
    → skill_id = "uscis-check"        (parent dir name of forms/)
    → form_id  = "uscis-check/example"  (only used in logs/listings)
    → profile  = fields.get("_bsession_profile", skill_id)
                 — defaults to skill_id so same-skill forms share Chrome/cookies

When invoked via the host bsession wrapper, the form file is staged
under /workspace/.bsession-staging/<skill_id>/forms/<basename>.toml so
the container can read it; the wrapper sets BSESSION_FORM to that
staged path, so this module just reads from disk like normal.

If no form path is provided, falls back to the __scratch__ context:
  skill_id = form_id = profile = "__scratch__"
"""

import os
import sys
import tomllib
from dataclasses import dataclass


SCRATCH = "__scratch__"


@dataclass(frozen=True)
class FormContext:
    form_path: str | None      # None for scratch
    skill_id: str
    form_id: str               # globally unique; used as tabs.form_id key
    profile: str
    fields: dict


def resolve(form_path=None, profile_override=None):
    """Build a FormContext from path + overrides.

    Resolution order for `profile`:
      1. profile_override (CLI --profile or env BSESSION_PROFILE)
      2. fields["_bsession_profile"] in the toml
      3. "default"
    """
    if form_path is None:
        form_path = os.environ.get("BSESSION_FORM")

    if not form_path:
        return FormContext(
            form_path=None, skill_id=SCRATCH, form_id=SCRATCH,
            profile=profile_override or SCRATCH, fields={},
        )

    if not os.path.isfile(form_path):
        print(f"form not found: {form_path}", file=sys.stderr)
        sys.exit(2)

    with open(form_path, "rb") as f:
        fields = tomllib.load(f)

    # skill_id = name of dir containing forms/ (or parent of toml if no forms/ dir)
    parent = os.path.dirname(os.path.abspath(form_path))
    if os.path.basename(parent) == "forms":
        skill_id = os.path.basename(os.path.dirname(parent))
    else:
        skill_id = os.path.basename(parent)

    base = os.path.splitext(os.path.basename(form_path))[0]
    form_id = f"{skill_id}/{base}"

    profile = (
        profile_override
        or os.environ.get("BSESSION_PROFILE")
        or fields.get("_bsession_profile")
        or skill_id
    )

    return FormContext(
        form_path=os.path.abspath(form_path),
        skill_id=skill_id,
        form_id=form_id,
        profile=profile,
        fields=fields,
    )


def get_field(ctx, key):
    """Return field value, exiting if missing."""
    if key not in ctx.fields:
        print(f"form '{ctx.form_path}' has no field '{key}'", file=sys.stderr)
        sys.exit(2)
    return ctx.fields[key]


def public_fields(ctx):
    """Fields excluding _bsession_* internals."""
    return {k: v for k, v in ctx.fields.items() if not k.startswith("_bsession")}
