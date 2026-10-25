"""Thin clients for Polymarket's public, unauthenticated APIs.

- Gamma API (market/event metadata): https://gamma-api.polymarket.com
- CLOB API (price history):          https://clob.polymarket.com
- Data API (trades, positions, leaderboard): https://data-api.polymarket.com
"""
import time

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

_session = requests.Session()
_session.headers["User-Agent"] = "polymkt-research/0.1"


def get_json(url, params=None, retries=5, timeout=60):
    """GET with exponential backoff on network errors, 429 and 5xx."""
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def iter_events(**filters):
    """Yield every event matching `filters`, following keyset pagination."""
    params = {"limit": 500, **filters}
    while True:
        page = get_json(f"{GAMMA}/events/keyset", params)
        yield from page.get("events") or []
        cursor = page.get("next_cursor")
        if not cursor or not page.get("events"):
            return
        params["after_cursor"] = cursor


def price_history(token_id, fidelity=1440):
    """Full price history for one outcome token. `fidelity` is bar size in minutes."""
    data = get_json(f"{CLOB}/prices-history",
                    {"market": token_id, "interval": "max", "fidelity": fidelity})
    return data.get("history", [])
