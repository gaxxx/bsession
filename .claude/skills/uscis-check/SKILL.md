---
name: uscis-check
description: Check USCIS case status via bsession (handles Cloudflare). Triggers on "USCIS", "case status", "EAD status", "查案件", "签证状态".
---

# USCIS Case Status

Check USCIS case status using bsession. Each case is a `.toml` file under `forms/` — one per receipt number.

## Available cases

| Form | Person | Receipt |
|------|--------|---------|
| `forms/example.toml` | (example placeholder) | WAC1234567890 |

Add real cases by dropping new `.toml` files into `forms/` with the same shape.

## Usage

For a single case:
```bash
bash .claude/skills/uscis-check/run.sh .claude/skills/uscis-check/forms/example.toml
```

For all cases (loop):
```bash
for f in .claude/skills/uscis-check/forms/*.toml; do
  bash .claude/skills/uscis-check/run.sh "$f"
done
```

## Routing

- "<person> <case-type> status" → matching `forms/<basename>.toml`
- "USCIS status" / "case status" without specifics → ask which case, or run all

## Output

Each run prints one JSON line:

```json
{"person": "...", "case_type": "I-765 EAD", "receipt_number": "WAC...", "status": "Was Approved", "detail": "..."}
```

If status changed from a previous run, highlight with ⚠️.

## Notes

- Cloudflare: bsession tries automatic bypass first. If it can't, solve manually at http://localhost:6080/vnc.html (VNC) — bsession will detect once you pass.
- Cookies persist in the profile, so Cloudflare typically only blocks the first run of the day.
