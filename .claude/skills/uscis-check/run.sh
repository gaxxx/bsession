#!/usr/bin/env bash
# Check one USCIS case. Pass form path as $1 (or set BSESSION_FORM).
set -euo pipefail

FORM="${1:-${BSESSION_FORM:-}}"
[[ -z "$FORM" ]] && { echo "usage: $0 <form.toml>" >&2; exit 2; }
[[ ! -f "$FORM" ]] && { echo "form not found: $FORM" >&2; exit 2; }

export BSESSION_FORM="$FORM"

RECEIPT=$(bsession form get receipt_number)

bsession nav https://egov.uscis.gov/casestatus/mycasestatus.do --wait 8
bsession bypass cloudflare
bsession wait 3

INPUT=$(bsession find 'textbox|text.*receipt|input')
bsession fill "$INPUT" "$RECEIPT"
bsession wait 1

BTN=$(bsession find '[Cc]heck [Ss]tatus|button.*[Ss]ubmit|button.*[Cc]heck')
bsession click "$BTN" --wait 5

STATUS=$(bsession extract 'heading "Case ([^"]*)"' --max-lines 1 --exclude "Status Online" 2>/dev/null || echo "(unknown)")
DETAIL=$(bsession extract 'text:.*(Form I-|approved|received|denied|transferred|petition).*' --max-lines 3 2>/dev/null || echo "")

bsession form dump | jq --arg s "$STATUS" --arg d "$DETAIL" '. + {status: $s, detail: $d}'
