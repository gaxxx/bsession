"""Bypass strategies for various restriction checks.

Each module handles a specific type of challenge:
  - cloudflare: Cloudflare Turnstile / challenge pages
  - (add new modules here as needed)
"""

from lib.bypass.cloudflare import is_cloudflare, wait_for_cloudflare
