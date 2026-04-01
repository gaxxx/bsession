---
name: skill-builder
description: Build reusable browser automation skills by exploring websites with bsession tools, then saving the workflow as a YAML skill for cheap replay.
---

# Browser Skill Builder

You build browser automation skills. You explore a website using bsession CLI tools, figure out the workflow, then save it as a reusable YAML skill that runs without AI.

## Browser Tools (via `bsession` CLI)

Run these inside the bsession container (`docker exec agent-browser ...`) or via the host wrapper `./bsession`:

```bash
bsession browse <url> [-w 8]      # open URL, print accessibility tree
bsession snapshot                   # print current page snapshot
bsession click <ref>                # click element, print new snapshot
bsession fill <ref> <value>         # clear + fill input
bsession type <ref> <text>          # type text character by character
bsession select <ref> <value>       # select dropdown option
bsession screenshot [-o file.png]   # save screenshot
bsession check-cf                   # detect + bypass Cloudflare
```

All commands accept `-p <port>` for the CDP port (default: 9222).

## How to Read Snapshots

The snapshot is an accessibility tree. Each line shows an element:
```
- heading "Page Title" [ref=e1]
- textbox "Email" [ref=e2]
- button "Submit" [ref=e3]
- combobox "Country" [ref=e4]
```

Use ref values (e.g. `e2`) with click, fill, select commands.

## Workflow: Build a New Skill

### Step 1: Explore the website

1. `bsession browse <url>` — see what's on the first page
2. Read the snapshot — identify form fields, buttons, navigation
3. Interact: `bsession fill <ref> <value>`, `bsession click <ref>`
4. After each action, read the new snapshot
5. Continue page by page until you reach the result

### Step 2: Record what worked

As you explore, note each action that succeeded:
- Which URL to start at
- What elements to find (by their roles/labels, not specific refs — refs change)
- What to fill, click, or select
- What to extract from the final page
- How to detect if it worked or failed

### Step 3: Save as YAML skill

Create a skill YAML file at `workspace/skills/<name>.yaml`:

```yaml
name: my_skill
description: "What this skill does"
version: "1.0.0"
tags: [monitor]

params:
  - name: MY_PARAM
    description: "What the user provides"
    required: true

steps:
  - action: navigate
    url: "https://example.com"
    wait: 5

  - action: bypass
    type: cloudflare

  - action: find
    name: my_input
    patterns:
      - textbox
      - "text.*email"

  - action: fill
    ref: "{{my_input}}"
    value: "{{MY_PARAM}}"

  - action: find
    name: submit_btn
    patterns:
      - "button.*[Ss]ubmit"

  - action: click
    ref: "{{submit_btn}}"
    wait: 5

  - action: extract
    name: result_text
    pattern: 'heading "([^"]*)"'
    max_lines: 1

result:
  title: "{{result_text}}"

monitor:                         # optional: run as recurring check
  interval: "3600"
  change_field: title
  notify:
    webhook: "{{WEBHOOK_URL}}"
```

### Step 4: Create the session conf

Create `workspace/conf/<name>.conf`:

```ini
[session]
skill = my_skill

[env]
MY_PARAM = value_here
CHECK_INTERVAL = 3600
WEBHOOK_URL =
```

### Step 5: Test and iterate

```bash
bsession skill run my_skill       # test once
bsession skill eval my_skill      # check success rate
```

If the skill breaks, re-explore the site to update patterns.

## Available Step Actions

| Action | Params | Use for |
|--------|--------|---------|
| `navigate` | `url`, `wait` | Open a page |
| `bypass` | `type` (cloudflare) | Handle bot protection |
| `find` | `name`, `patterns[]`, `optional` | Locate element by label/role patterns |
| `fill` | `ref`, `value` | Fill text input |
| `clear` | `ref` | Clear input |
| `click` | `ref`, `wait` | Click button/link |
| `type` | `ref`, `value` | Type character by character |
| `select` | `ref`, `value`, `skip_if_empty` | Pick dropdown option |
| `extract` | `name`, `pattern`, `exclude`, `max_lines` | Pull text from page via regex |
| `wait` | `seconds` | Pause |
| `wait_for` | `pattern`, `timeout`, `interval` | Poll until content appears |
| `transform` | `name`, `source`, `operation` | Clean up extracted text |
| `snapshot` | `name` | Store a snapshot for later steps |
| `check` | `snapshot_pattern`, `error` | Assert something is on the page |

## Key Principles

- **Find by patterns, not refs**: Refs change between page loads. Use `find` with label/role patterns like `textbox`, `button.*Submit`, etc.
- **Variable references**: Use `{{var_name}}` to reference params and previous step outputs.
- **Bypass first**: Always add a `bypass` step after navigating to handle Cloudflare.
- **Extract with regex**: The snapshot uses `heading "text"`, `text: "content"` format. Write regex patterns against this format.
- **Monitor mode**: Add a `monitor` section to run the skill repeatedly and notify on changes.

## Export

After building a skill, export it for other systems:

```bash
bsession skill export my_skill -f claude     # Claude Code skill (.md)
bsession skill export my_skill -f openclaw   # OpenClaw tool (JSON)
```
