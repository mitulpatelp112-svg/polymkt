"""Rank wallets by trading profit over the study window (default: past year).

Polymarket's leaderboard only offers day/week/month/all-time windows, so this
builds a candidate pool from the all-time leaderboards (by profit and by
volume) plus every wallet behind a $1M+ trade, then reads each wallet's daily
cumulative P&L series and takes the change over the window:

    profit = (realized + unrealized P&L) at the end
           - (realized + unrealized P&L) at the start of the window

A wallet that earned a lot in the window but lost more than that before it can
sit outside the all-time top lists and be missed; the leaderboard floor is
reported so you can judge how likely that is.

Output: data/trader_pnl.parquet (every candidate) and reports/top_traders.md

    python -m polymkt.top_traders [--start 2025-10-25] [--top 100] [--pool 6000]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

from .analyze import REPORTS, usd
from .api import DATA_API, get_json
from .fetch_markets import DATA


def leaderboard(sort_by, pool):
    rows, params = [], {"time_period": "all", "limit": 100, "sort_by": sort_by}
    while len(rows) < pool:
        page = get_json(f"{DATA_API}/v2/leaderboard", params)
        rows += page["data"]
        if not page["pagination"].get("has_more"):
            break
        params["cursor"] = page["pagination"]["next_cursor"]
    return rows[:pool]


def window_pnl(wallet, start):
    pts = get_json(f"{DATA_API}/v2/user-pnl",
                   {"user": wallet, "interval": "all", "fidelity": "1d"})["data"]["points"]
    if not pts:
        return None
    df = pd.DataFrame(pts)
    df["pnl"] = df.realized_pnl.fillna(0) + df.unrealized_pnl.fillna(0)
    before = df[df.timestamp < start.timestamp()]
    # A wallet that started trading inside the window starts from zero.
    base = before.iloc[-1] if len(before) else None
    end = df.iloc[-1]
    return {
        "wallet": wallet,
        "profit": end.pnl - (base.pnl if base is not None else 0),
        "realized": end.realized_pnl - (base.realized_pnl if base is not None else 0),
        "volume": end.volume - (base.volume if base is not None else 0),
        "trades": end.trade_count - (base.trade_count if base is not None else 0),
        "lifetime_profit": end.pnl,
        "first_seen": pd.to_datetime(df.timestamp.iloc[0], unit="s", utc=True),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default=(datetime.now(timezone.utc) - timedelta(days=365))
                    .date().isoformat())
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--pool", type=int, default=6000, help="wallets taken from each leaderboard")
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()
    start = pd.Timestamp(args.start, tz="UTC")

    by_pnl = leaderboard("PNL", args.pool)
    by_vol = leaderboard("VOLUME", args.pool)
    names = {r["user_id"]: r.get("user_name") or "" for r in by_vol + by_pnl}
    wallets = set(names)
    big = DATA / "big_trades.parquet"
    if big.exists():
        bt = pd.read_parquet(big, columns=["proxyWallet", "name"])
        for w, n in bt.itertuples(index=False):
            names.setdefault(w, n or "")
        wallets |= set(bt.proxyWallet)
    print(f"{len(wallets):,} candidate wallets (all-time profit floor "
          f"{usd(by_pnl[-1]['pnl'])}, volume floor {usd(by_vol[-1]['volume'])})", flush=True)

    def safe(w):
        try:
            return window_pnl(w, start)
        except Exception as e:  # skip; reported below
            print(f"  {w} failed: {e}", flush=True)
            return None

    with ThreadPoolExecutor(args.workers) as pool:
        rows = [r for r in pool.map(safe, sorted(wallets)) if r]
    df = pd.DataFrame(rows)
    df["name"] = df.wallet.map(names)
    df = df.sort_values("profit", ascending=False).reset_index(drop=True)
    df.to_parquet(DATA / "trader_pnl.parquet", index=False)

    top = df.head(args.top)
    out = [f"# Top {args.top} Polymarket traders by profit, {args.start} to "
           f"{datetime.now(timezone.utc):%Y-%m-%d}\n",
           "Profit is the change in each wallet's realized + unrealized trading P&L over "
           "the window, from Polymarket's public per-wallet P&L series "
           "(`/v2/user-pnl`). Unrealized P&L marks open positions at current prices, so "
           "it can still move. Rewards and rebates are excluded. Names are self-chosen "
           "Polymarket usernames; one person can run several wallets.\n",
           f"Candidates: {len(df):,} wallets from the all-time profit and volume "
           f"leaderboards (top {args.pool:,} each) plus every wallet with a $1M+ trade. "
           f"The top {args.top} together made **{usd(top.profit.sum())}**.\n",
           "| # | Trader | Profit | Realized | Volume | Trades | Lifetime profit |",
           "|---:|---|---:|---:|---:|---:|---:|"]
    for i, r in enumerate(top.itertuples(), 1):
        # Wallets without a username get a default like "0xABC...-1759935795465".
        name = r.name if r.name and not r.name.startswith("0x") else r.wallet[:10] + "..."
        name = name.replace("|", "/")
        out.append(f"| {i} | [{name}](https://polymarket.com/profile/{r.wallet}) | "
                   f"{usd(r.profit)} | {usd(r.realized)} | {usd(r.volume)} | "
                   f"{int(r.trades):,} | {usd(r.lifetime_profit)} |")
    REPORTS.mkdir(exist_ok=True)
    top.to_csv(REPORTS / "top_traders.csv", index=False)
    (REPORTS / "top_traders.md").write_text("\n".join(out) + "\n")
    print(f"Wrote {REPORTS / 'top_traders.md'}")


if __name__ == "__main__":
    main()
