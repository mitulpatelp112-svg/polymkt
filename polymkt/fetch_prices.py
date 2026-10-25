"""Download price history (the "Yes"/first-outcome token) for markets in data/markets.parquet.

Only markets with lifetime volume >= --min-volume are fetched: the year holds
millions of tiny markets (sports props, 5-minute crypto up/down) whose
histories add little and would take a day or more to pull. At ~60 requests/s,
$100K (default) is ~95K markets / ~30 min; $10K is ~420K markets / ~2 hours.
Output is data/prices.parquet with columns market_id, t (UTC timestamp), p (price 0..1).
Reruns skip markets already fetched, so lowering --min-volume later only
fetches the new ones.

    python -m polymkt.fetch_prices [--min-volume 100000] [--fidelity 1440] [--workers 32]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from .api import price_history
from .fetch_markets import DATA

OUT = DATA / "prices.parquet"
PARTS = DATA / "prices_parts"
DONE = PARTS / "done.txt"  # market ids fetched, including ones with empty history
CHUNK = 2000


def fetch_one(market_id, token, fidelity):
    hist = price_history(token, fidelity=fidelity)
    return pd.DataFrame({"market_id": market_id,
                         "t": [h["t"] for h in hist],
                         "p": [h["p"] for h in hist]})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--min-volume", type=float, default=100_000, help="USD lifetime volume floor")
    ap.add_argument("--fidelity", type=int, default=1440, help="bar size in minutes (1440 = daily)")
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()

    markets = pd.read_parquet(DATA / "markets.parquet", columns=["id", "yes_token", "volumeNum"])
    todo = markets[(markets.volumeNum >= args.min_volume) & markets.yes_token.notna()]
    PARTS.mkdir(parents=True, exist_ok=True)
    done = set(DONE.read_text().split()) if DONE.exists() else set()
    todo = todo[~todo.id.astype(str).isin(done)].sort_values("volumeNum", ascending=False)
    print(f"{len(todo):,} markets to fetch ({len(done):,} already done)", flush=True)

    failed = 0
    with ThreadPoolExecutor(args.workers) as pool:
        for i in range(0, len(todo), CHUNK):
            chunk = todo.iloc[i:i + CHUNK]
            futs = {pool.submit(fetch_one, str(r.id), r.yes_token, args.fidelity): str(r.id)
                    for r in chunk.itertuples()}
            frames, ok = [], []
            for fut in as_completed(futs):
                try:
                    frames.append(fut.result())
                    ok.append(futs[fut])
                except Exception as e:  # keep going; a rerun retries failures
                    failed += 1
                    print(f"  {futs[fut]} failed: {e}", flush=True)
            # Name by first market id so chunks from later reruns never collide.
            name = f"chunk_{chunk.id.iloc[0]}_{i:07d}.parquet"
            pd.concat(frames, ignore_index=True).to_parquet(PARTS / name, index=False)
            with DONE.open("a") as f:
                f.write("\n".join(ok) + "\n")
            print(f"  {min(i + CHUNK, len(todo)):,}/{len(todo):,}", flush=True)

    prices = pd.concat([pd.read_parquet(p) for p in sorted(PARTS.glob("*.parquet"))],
                       ignore_index=True).drop_duplicates(["market_id", "t"])
    prices["t"] = pd.to_datetime(prices["t"], unit="s", utc=True)
    prices.to_parquet(OUT, index=False)
    print(f"Saved {len(prices):,} price points for {prices.market_id.nunique():,} markets "
          f"to {OUT} ({failed} failures; rerun to retry)")


if __name__ == "__main__":
    main()
