"""Notification helpers — webhooks and future notification channels."""

import json
import urllib.request


def send_webhook(url, payload):
    """POST a JSON payload to a webhook URL. Returns True on success."""
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False
