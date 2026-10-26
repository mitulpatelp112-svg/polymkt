"""Download the trade history of the top wallets in reports/top_traders.csv.

Walks each wallet's activity feed one week at a time over the study window.
The feed caps offsets at 5,000, and the busiest wallets (bots) make millions
of trades a year, so each week keeps at most --max-per-week trades (the
earliest in that week). Weeks that hit the cap are recorded in
data/wallet_weeks.parquet so analyses can tell sampled wallets from complete
ones.

Output: data/wallet_trades.parquet, data/wallet_weeks.parquet. With --losers N,
fetches the N biggest losers in data/trader_pnl.parquet instead, into
data/loser_trades.parquet / data/loser_weeks.parquet, as a control group:
any pattern found among winners only means something if losers don't share it.

    python -m polymkt.fetch_wallet_trades [--start 2025-10-25] [--max-per-week 2000] [--losers 100]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

from .analyze import REPORTS
from .api import DATA_API, get_json
from .fetch_markets import DATA

COLS = ["timestamp", "side", "size", "usdcSize", "price", "conditionId", "outcome",
        "outcomeIndex", "title", "eventSlug", "transactionHash"]
PAGE = 500


def week_trades(wallet, lo, hi, cap):
    rows = []
    while len(rows) < cap:
        page = get_json(f"{DATA_API}/activity", {
            "user": wallet, "type": "TRADE", "start": lo, "end": hi - 1, "limit": PAGE,
            "offset": len(rows), "sortBy": "TIMESTAMP", "sortDirection": "ASC"})
        rows += page
        if len(page) < PAGE:
            return rows, False
    return rows[:cap], True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default=(datetime.now(timezone.utc) - timedelta(days=365))
                    .date().isoformat())
    ap.add_argument("--max-per-week", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--losers", type=int, default=0, help="fetch the N biggest losers instead")
    args = ap.parse_args()

    if args.losers:
        top = pd.read_parquet(DATA / "trader_pnl.parquet").nsmallest(args.losers, "profit")
        prefix = "loser"
    else:
        top = pd.read_csv(REPORTS / "top_traders.csv")
        prefix = "wallet"
    start = int(pd.Timestamp(args.start, tz="UTC").timestamp())
    now = int(datetime.now(timezone.utc).timestamp())
    weeks = list(range(start, now, 7 * 86400))
    tasks = [(w, lo, min(lo + 7 * 86400, now)) for w in top.wallet for lo in weeks]
    print(f"{len(top)} wallets x {len(weeks)} weeks", flush=True)

    def run(task):
        wallet, lo, hi = task
        rows, truncated = week_trades(wallet, lo, hi, args.max_per_week)
        return wallet, lo, rows, truncated

    trades, weeks_meta, bios = [], [], {}
    with ThreadPoolExecutor(args.workers) as pool:
        for n, (wallet, lo, rows, truncated) in enumerate(pool.map(run, tasks), 1):
            trades += [{"wallet": wallet, **{c: r.get(c) for c in COLS}} for r in rows]
            weeks_meta.append({"wallet": wallet, "week": lo, "n": len(rows),
                               "truncated": truncated})
            if rows and rows[0].get("bio"):
                bios[wallet] = rows[0]["bio"]
            if n % 1000 == 0:
                print(f"  {n:,}/{len(tasks):,} wallet-weeks, {len(trades):,} trades", flush=True)

    df = pd.DataFrame(trades).drop_duplicates()
    df["time"] = pd.to_datetime(df.timestamp, unit="s", utc=True)
    df.to_parquet(DATA / f"{prefix}_trades.parquet", index=False)
    meta = pd.DataFrame(weeks_meta)
    meta["bio"] = meta.wallet.map(bios)
    meta.to_parquet(DATA / f"{prefix}_weeks.parquet", index=False)
    sampled = meta.groupby("wallet").truncated.any().sum()
    print(f"Saved {len(df):,} trades for {df.wallet.nunique()} wallets "
          f"({sampled} wallets sampled because they hit the weekly cap)")


if __name__ == "__main__":
    main()
