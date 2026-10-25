"""Download metadata for every Polymarket market active in the study window.

A market is in the window if its end date is on or after `--start` (default:
one year ago). That covers markets that resolved during the year and markets
still open today. Output: data/markets.parquet (one row per market) with the
parent event's title and tags attached.

    python -m polymkt.fetch_markets [--start 2025-10-25] [--workers 8]
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .api import iter_events

DATA = Path(__file__).resolve().parent.parent / "data"
PARTS = DATA / "markets_parts"

MARKET_COLS = [
    "id", "conditionId", "question", "slug", "groupItemTitle", "createdAt",
    "startDate", "endDate", "closedTime", "active", "closed", "archived",
    "negRisk", "umaResolutionStatus", "volumeNum", "volumeClob", "volume1yr",
    "liquidityNum", "lastTradePrice", "bestBid", "bestAsk", "spread",
]


def _loads(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def flatten(event):
    tags = [t.get("label") for t in event.get("tags") or [] if t.get("label")]
    for m in event.get("markets") or []:
        row = {c: m.get(c) for c in MARKET_COLS}
        outcomes = _loads(m.get("outcomes")) or []
        prices = _loads(m.get("outcomePrices")) or []
        tokens = _loads(m.get("clobTokenIds")) or []
        row.update(
            event_id=event.get("id"),
            event_title=event.get("title"),
            event_slug=event.get("slug"),
            tags="|".join(tags),
            outcomes=json.dumps(outcomes),
            outcome_prices=json.dumps(prices),
            clob_token_ids=json.dumps(tokens),
            yes_token=tokens[0] if tokens else None,
        )
        # A closed market's final outcome price is 1 for the winner and 0 for
        # the loser; anything else (e.g. 0.5) means unresolved or voided.
        try:
            p0 = float(prices[0])
            row["resolved_yes"] = 1.0 if p0 >= 0.99 else 0.0 if p0 <= 0.01 else None
        except (IndexError, TypeError, ValueError):
            row["resolved_yes"] = None
        if not m.get("closed"):
            row["resolved_yes"] = None
        yield row


def fetch_window(lo, hi):
    """All markets of events ending in [lo, hi); hi=None means open-ended."""
    filters = {"end_date_min": f"{lo}T00:00:00Z"}
    if hi:
        filters["end_date_max"] = f"{hi}T00:00:00Z"
    return [row for ev in iter_events(**filters) for row in flatten(ev)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    today = datetime.now(timezone.utc).date()
    ap.add_argument("--start", default=(today - timedelta(days=365)).isoformat(),
                    help="earliest end date (YYYY-MM-DD)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    # Keyset pagination is sequential, so split the year into one-day windows
    # and fetch them in parallel. Each window is cached so reruns resume. The
    # last window is open-ended to pick up markets ending in the future, and is
    # always refetched since it changes.
    days = pd.date_range(args.start, today, freq="D").date
    windows = [(d.isoformat(), (d + timedelta(days=1)).isoformat()) for d in days[:-1]]
    windows.append((today.isoformat(), None))
    PARTS.mkdir(parents=True, exist_ok=True)

    def run(win):
        path = PARTS / f"{win[0]}.parquet"
        if win[1] and path.exists():
            return
        pd.DataFrame(fetch_window(*win)).to_parquet(path, index=False)

    with ThreadPoolExecutor(args.workers) as pool:
        for n, _ in enumerate(pool.map(run, windows), 1):
            if n % 20 == 0:
                print(f"  {n}/{len(windows)} days", flush=True)

    df = pd.concat([pd.read_parquet(p) for p in sorted(PARTS.glob("*.parquet"))],
                   ignore_index=True)
    # Windows share their boundary instant, so a market can appear twice.
    df = df.drop_duplicates("id", keep="last")
    for c in ("createdAt", "startDate", "endDate", "closedTime"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce", format="mixed")
    for c in ("volumeNum", "volumeClob", "volume1yr", "liquidityNum", "lastTradePrice",
              "bestBid", "bestAsk", "spread"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # An event ending in the window can hold sub-markets that ended earlier.
    df = df[df.endDate >= pd.Timestamp(args.start, tz="UTC")]

    DATA.mkdir(exist_ok=True)
    out = DATA / "markets.parquet"
    df.to_parquet(out, index=False)
    print(f"Saved {len(df):,} markets from {df['event_id'].nunique():,} events to {out}")


if __name__ == "__main__":
    main()
