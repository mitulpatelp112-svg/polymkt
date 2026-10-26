"""Find every single trade of >= --min-cash USD (size x price) in the study window.

The global trades feed only reaches back a few weeks, so this queries each
market with lifetime volume >= --min-cash (a market can't hold a bigger trade
than its volume) for trades above the threshold. One order can fill as several
trades, so a big bet split across fills under the threshold is not counted.

Output: data/big_trades.parquet

    python -m polymkt.fetch_big_trades [--min-cash 100000] [--start 2025-10-25] [--workers 32]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

from .analyze import REPORTS, usd
from .api import DATA_API, get_json
from .fetch_markets import DATA

COLS = ["proxyWallet", "name", "pseudonym", "side", "outcome", "size", "price",
        "timestamp", "title", "eventSlug", "conditionId", "transactionHash"]


def big_trades(condition_id, min_cash):
    rows, offset = [], 0
    while True:
        page = get_json(f"{DATA_API}/trades", {"market": condition_id, "filterType": "CASH",
                                               "filterAmount": min_cash, "limit": 1000,
                                               "offset": offset})
        rows += page
        if len(page) < 1000:
            return rows
        offset += 1000


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--min-cash", type=float, default=100_000)
    ap.add_argument("--start", default=(datetime.now(timezone.utc) - timedelta(days=365))
                    .date().isoformat(), help="drop trades before this date")
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()

    markets = pd.read_parquet(DATA / "markets.parquet", columns=["conditionId", "volumeNum", "tags"])
    todo = markets[markets.volumeNum >= args.min_cash].dropna(subset=["conditionId"])
    print(f"Scanning {len(todo):,} markets", flush=True)

    rows = []
    with ThreadPoolExecutor(args.workers) as pool:
        found = pool.map(lambda c: big_trades(c, args.min_cash), todo.conditionId)
        for n, page in enumerate(found, 1):
            rows += [{c: t.get(c) for c in COLS} for t in page]
            if n % 5000 == 0:
                print(f"  {n:,}/{len(todo):,} markets, {len(rows):,} trades", flush=True)

    df = pd.DataFrame(rows, columns=COLS).drop_duplicates()
    df["cash"] = df["size"] * df["price"]
    df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    # Markets ending in the window can have trades from before it.
    df = df[df.time >= pd.Timestamp(args.start, tz="UTC")]
    df = df.merge(todo[["conditionId", "tags"]], on="conditionId", how="left")
    df.to_parquet(DATA / "big_trades.parquet", index=False)
    print(f"Saved {len(df):,} trades of >= ${args.min_cash:,.0f} to {DATA / 'big_trades.parquet'}")
    write_report(df, markets[markets.volumeNum >= args.min_cash], args.min_cash)


def write_report(df, big_markets, min_cash):
    df = df.sort_values("cash", ascending=False)
    REPORTS.mkdir(exist_ok=True)
    df.drop(columns=["timestamp"]).to_csv(REPORTS / "big_trades.csv", index=False)
    near_certain = (df.price >= 0.99).mean()
    out = [f"# Single trades of {usd(min_cash)} or more\n",
           f"**{len(df):,} trades** worth **{usd(df.cash.sum())}** from "
           f"**{df.proxyWallet.nunique():,} wallets**, {df.time.min():%Y-%m-%d} to "
           f"{df.time.max():%Y-%m-%d}. Found by scanning all {len(big_markets):,} markets "
           f"in the window with at least {usd(min_cash)} of lifetime volume. Cash = shares x price. One order can fill as "
           "several smaller trades, so big bets split that way are not counted.\n",
           f"{near_certain:.0%} of these were at 99c or higher: large money parked on "
           "near-certain outcomes for a sub-1% return, or exiting such positions.\n",
           f"## Biggest whales by {usd(min_cash)}+ trades\n",
           "| Trader | Trades | Total |\n|---|---:|---:|"]
    by = df.groupby(["proxyWallet", "name"], dropna=False).cash.agg(["size", "sum"])
    for (w, n), r in by.sort_values("sum", ascending=False).head(20).iterrows():
        n = n if n and not str(n).startswith("0x") else w[:10] + "..."
        out.append(f"| [{n}](https://polymarket.com/profile/{w}) | {int(r['size'])} | {usd(r['sum'])} |")
    out += ["", "## The 500 largest trades\n" if len(df) > 500 else "## Every trade\n",
            "| Date | Trader | Side | Outcome | Price | Cash | Market |",
            "|---|---|---|---|---:|---:|---|"]
    for r in df.head(500).itertuples():
        n = r.name if r.name and not str(r.name).startswith("0x") else r.proxyWallet[:10] + "..."
        out.append(f"| {r.time:%Y-%m-%d} | [{n}](https://polymarket.com/profile/{r.proxyWallet}) "
                   f"| {r.side} | {r.outcome} | {r.price:.3f} | {usd(r.cash)} | "
                   f"[{str(r.title).replace('|', '/')}](https://polymarket.com/event/{r.eventSlug}) |")
    (REPORTS / "big_trades.md").write_text("\n".join(out) + "\n")
    print(f"Wrote {REPORTS / 'big_trades.md'}")


if __name__ == "__main__":
    main()
