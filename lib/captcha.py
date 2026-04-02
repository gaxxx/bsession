"""Captcha snapshot service — capture the captcha area as a cropped PNG image.

Uses CDP to locate the Cloudflare Turnstile iframe (or similar captcha container),
get its bounding rectangle, and capture a clipped screenshot of just that region.
"""

import base64
import json
import subprocess
import urllib.request

# CSS selectors to try when locating captcha elements on the page.
CAPTCHA_SELECTORS = [
    "iframe[src*='challenges.cloudflare']",
    "iframe[src*='turnstile']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='recaptcha']",
    "iframe[src*='captcha']",
    ".cf-turnstile",
    "#cf-turnstile",
    "[data-turnstile]",
    ".h-captcha",
    ".g-recaptcha",
    "#captcha",
    ".captcha",
]

# Padding (px) added around the detected element when clipping.
CLIP_PADDING = 10


def _cdp_ws_url(cdp_port: int) -> str:
    """Get the WebSocket debugger URL for the first page target."""
    resp = urllib.request.urlopen(
        f"http://localhost:{cdp_port}/json", timeout=5,
    )
    targets = json.loads(resp.read())
    page_targets = [t for t in targets if t.get("type") == "page"]
    if not page_targets:
        raise RuntimeError(f"No page targets on CDP port {cdp_port}")
    return page_targets[0]["webSocketDebuggerUrl"]


def _run_cdp_script(ws_url: str, script: str, timeout: int = 10) -> str:
    """Execute a Node.js CDP script and return its stdout."""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"CDP script failed: {result.stderr.decode(errors='replace')}"
        )
    return result.stdout.decode(errors="replace").strip()


def find_captcha_bounds(cdp_port: int) -> dict | None:
    """Locate the captcha element and return its bounding box.

    Returns dict with keys {x, y, width, height} in CSS pixels, or None
    if no captcha element is found.
    """
    ws_url = _cdp_ws_url(cdp_port)

    # Build a JS snippet that tries each selector until one matches,
    # then returns its bounding rect as JSON.
    selectors_js = json.dumps(CAPTCHA_SELECTORS)
    js_code = f"""
    (function() {{
        const selectors = {selectors_js};
        for (const sel of selectors) {{
            let el = document.querySelector(sel);
            if (el) {{
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {{
                    return JSON.stringify({{
                        x: r.x, y: r.y, width: r.width, height: r.height
                    }});
                }}
            }}
        }}
        return null;
    }})()
    """

    eval_js = json.dumps(js_code)
    node_script = (
        f"const ws=new WebSocket('{ws_url}');"
        "ws.onopen=()=>ws.send(JSON.stringify("
        f"  {{id:1,method:'Runtime.evaluate',params:{{expression:{eval_js},returnByValue:true}}}}"
        "));"
        "ws.onmessage=e=>{"
        "  const r=JSON.parse(e.data);"
        "  if(r.id===1){process.stdout.write(String(r.result?.result?.value??''));ws.close();}"
        "};"
        "ws.onerror=e=>{process.stderr.write(String(e));process.exit(1);};"
    )

    raw = _run_cdp_script(ws_url, node_script)
    if not raw or raw == "null" or raw == "undefined":
        return None

    return json.loads(raw)


def capture_captcha_screenshot(cdp_port: int, padding: int = CLIP_PADDING) -> bytes:
    """Capture a PNG screenshot of just the captcha area.

    Raises RuntimeError if no captcha element is found on the page.
    Returns raw PNG bytes.
    """
    bounds = find_captcha_bounds(cdp_port)
    if bounds is None:
        raise RuntimeError("No captcha element found on the page")

    ws_url = _cdp_ws_url(cdp_port)

    # Apply padding, clamping to non-negative origin.
    x = max(0, bounds["x"] - padding)
    y = max(0, bounds["y"] - padding)
    width = bounds["width"] + padding * 2
    height = bounds["height"] + padding * 2

    clip_json = json.dumps({"x": x, "y": y, "width": width, "height": height, "scale": 1})
    node_script = (
        f"const ws=new WebSocket('{ws_url}');"
        "ws.onopen=()=>ws.send(JSON.stringify("
        f"  {{id:1,method:'Page.captureScreenshot',params:{{format:'png',clip:{clip_json}}}}}"
        "));"
        "ws.onmessage=e=>{"
        "  const r=JSON.parse(e.data);"
        "  if(r.id===1){process.stdout.write(r.result.data);ws.close();}"
        "};"
        "ws.onerror=e=>{process.stderr.write(String(e));process.exit(1);};"
    )

    result = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"CDP captcha screenshot failed: {result.stderr.decode(errors='replace')}"
        )
    return base64.b64decode(result.stdout)
