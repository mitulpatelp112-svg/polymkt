"""Screen for advance-knowledge trading outside sports, crypto and weather.

Insider trading on a prediction market looks like large buys of an unlikely
outcome shortly before it happens. To test that without only looking at the
winners, this takes every resolved non-sports market where one outcome traded
at <= --longshot between 1 and 3 days before settlement (from
data/prices.parquet), fetches all its trades of >= --min-cash, and keeps buys
at <= --longshot in the final --hours before settlement. If late long-shot
money wins no more often than its prices imply, there is no sign of
widespread inside information; individual wallets that keep winning upsets
are leads.

Output: data/longshot_trades.parquet, data/longshot_positions.parquet,
reports/insider_screen.md

    python -m polymkt.insider_screen [--min-cash 5000] [--longshot 0.35] [--hours 72]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .analyze import REPORTS, categorize, usd
from .api import DATA_API, get_json
from .fetch_markets import DATA
from .strategies import disp, edge


def market_trades(condition_id, min_cash):
    rows, offset = [], 0
    while True:
        page = get_json(f"{DATA_API}/trades", {"market": condition_id, "filterType": "CASH",
                                               "filterAmount": min_cash, "limit": 1000,
                                               "offset": offset})
        rows += page
        if len(page) < 1000 or offset >= 9000:
            return rows
        offset += 1000


def first_trade(wallet):
    page = get_json(f"{DATA_API}/activity", {"user": wallet, "limit": 1,
                                             "sortBy": "TIMESTAMP", "sortDirection": "ASC"})
    return pd.to_datetime(page[0]["timestamp"], unit="s", utc=True) if page else pd.NaT


def candidates(longshot):
    m = pd.read_parquet(DATA / "markets.parquet", columns=[
        "id", "conditionId", "question", "tags", "closedTime", "resolved_yes", "outcomes"])
    m = m[m.resolved_yes.notna() & m.closedTime.notna()].copy()
    m["id"] = m.id.astype(str)
    m["category"] = m.tags.map(categorize)
    m = m[~m.category.isin(["Sports", "Crypto", "Weather"])]
    pr = pd.read_parquet(DATA / "prices.parquet")
    j = pr.merge(m[["id", "closedTime"]], left_on="market_id", right_on="id")
    h = (j.closedTime - j.t).dt.total_seconds() / 3600
    rng = j[h.between(24, 72)].groupby("market_id").p.agg(["min", "max"])
    keep = rng[(rng["min"] <= longshot) | (rng["max"] >= 1 - longshot)].index
    return m[m.id.isin(keep)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--min-cash", type=float, default=5_000)
    ap.add_argument("--longshot", type=float, default=0.35)
    ap.add_argument("--hours", type=float, default=72)
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args()

    m = candidates(args.longshot)
    print(f"Scanning {len(m):,} markets", flush=True)
    with ThreadPoolExecutor(args.workers) as pool:
        found = pool.map(lambda c: market_trades(c, args.min_cash), m.conditionId)
        rows = [t for page in found for t in page]
    t = pd.DataFrame(rows).merge(m, on="conditionId", how="inner")
    t["time"] = pd.to_datetime(t.timestamp, unit="s", utc=True)
    t["usd"] = t["size"] * t.price
    t["h_to_close"] = (t.closedTime - t.time).dt.total_seconds() / 3600
    t.to_parquet(DATA / "longshot_trades.parquet", index=False)

    b = t[(t.side == "BUY") & (t.price <= args.longshot) & t.h_to_close.between(0, args.hours)
          & t.outcomeIndex.isin([0, 1])]
    b = b.assign(won=((b.outcomeIndex == 0) == (b.resolved_yes == 1)).astype(float))
    p = b.groupby(["proxyWallet", "conditionId", "outcomeIndex"]).agg(
        usd=("usd", "sum"), shares=("size", "sum"), won=("won", "first"),
        category=("category", "first"), q=("question", "first"), first=("time", "min"),
        h=("h_to_close", "max"), name=("name", "first")).reset_index()
    p["price"] = p.usd / p.shares
    p["pnl"] = p.shares * p.won - p.usd
    p = p.rename(columns={"proxyWallet": "wallet"})

    n, wr, avgp, z = edge(p)
    out = ["# Insider screen: late long-shot bets outside sports and crypto\n",
           f"Markets screened: **{len(m):,}** resolved politics, geopolitics, economy, "
           f"elections, culture and tech markets where one outcome traded at "
           f"{args.longshot * 100:.0f}c or less 1-3 days before settlement. Positions: "
           f"every wallet that bought that kind of long shot with {usd(args.min_cash)}+ "
           f"trades in the final {args.hours:.0f} hours.\n",
           "## Overall\n",
           f"{n:,} positions worth {usd(p.usd.sum())} won **{wr:.1%}** of the time at an "
           f"average price of **{avgp:.2f}** (edge z = **{z:+.1f}**). If held to "
           f"settlement they returned {usd(p.pnl.sum())} in total.\n",
           "| Category | Positions | Avg price | Win rate | Edge z | $ |",
           "|---|---:|---:|---:|---:|---:|"]
    for k, d in sorted(p.groupby("category"), key=lambda kv: -kv[1].usd.sum()):
        n_, wr_, p_, z_ = edge(d)
        out.append(f"| {k} | {n_:,} | {p_:.2f} | {wr_:.1%} | {z_:+.1f} | {usd(d.usd.sum())} |")
    out.append("")

    out.append("## By how close to settlement\n")
    out.append("| Bought | Positions | Avg price | Win rate | Edge z |\n|---|---:|---:|---:|---:|")
    p["when"] = pd.cut(p.h, [0, 1, 6, 24, args.hours],
                       labels=["last hour", "1-6 h", "6-24 h", f"24-{args.hours:.0f} h"])
    for k, d in p.groupby("when", observed=True):
        n_, wr_, p_, z_ = edge(d)
        out.append(f"| {k} | {n_:,} | {p_:.2f} | {wr_:.1%} | {z_:+.1f} |")
    out.append("")

    # Positions on one market win or lose together, so check how much of the
    # edge survives without the few markets with the most winning positions.
    mk = p.groupby("q").agg(pos=("won", "size"), won=("won", "max"), price=("price", "mean"))
    top3 = mk[mk.won == 1].nlargest(3, "pos")
    n_, wr_, p_, z_ = edge(p[~p.q.isin(top3.index)])
    mvar = (mk.price * (1 - mk.price)).sum()
    out.append("## Is it broad, or a few events?\n")
    out.append(f"Per market rather than per position: {len(mk):,} markets, {int(mk.won.sum())} "
               f"long shots came in versus {mk.price.sum():.1f} expected (z = "
               f"{(mk.won.sum() - mk.price.sum()) / np.sqrt(mvar):+.1f}). The markets with "
               "the most winning positions:\n")
    out.append("| Market | Late long-shot positions |\n|---|---:|")
    for q, r in top3.iterrows():
        out.append(f"| {q} | {int(r.pos)} |")
    out.append(f"\nWithout those {len(top3)}: {n_:,} positions won {wr_:.1%} at {p_:.2f} "
               f"(z = **{z_:+.1f}**).\n")

    with ThreadPoolExecutor(args.workers) as pool:
        ws = p.wallet.unique()
        firsts = dict(zip(ws, pool.map(first_trade, ws)))
    p["account_age_days"] = [(f - firsts[w]).total_seconds() / 86400
                             for w, f in zip(p.wallet, p["first"])]
    p["group"] = np.where(p.q.isin(top3.index), "top winning events",
                          np.where(p.won == 1, "other winners", "losers"))
    out.append("Fresh accounts are a classic insider tell, so compare buyers' account age "
               "(days from the wallet's first-ever trade to this bet):\n")
    out.append("| Buyers in | Positions | Account under 7 days old | Median age (days) |\n"
               "|---|---:|---:|---:|")
    for k, d in p.groupby("group"):
        out.append(f"| {k} | {len(d):,} | {(d.account_age_days < 7).mean():.0%} | "
                   f"{d.account_age_days.median():.0f} |")
    out.append("")
    p.to_parquet(DATA / "longshot_positions.parquet", index=False)

    w = p.groupby("wallet").agg(name=("name", "first"), bets=("won", "size"),
                                wins=("won", "sum"), expected=("price", "sum"),
                                usd=("usd", "sum"), pnl=("pnl", "sum"))
    var = p.assign(v=p.price * (1 - p.price)).groupby("wallet").v.sum()
    w["z"] = (w.wins - w.expected) / np.sqrt(var)
    rep = w[w.bets >= 3].sort_values("z", ascending=False)
    out.append("## Wallets that keep winning long shots (3+ bets)\n")
    out.append("One lucky upset is common; winning several at long odds is not. With "
               f"{len(rep):,} wallets tested, a few z-scores above 3 are expected by chance, "
               "so treat these as leads to check against the news timeline.\n")
    out.append("| Trader | Bets | Won | Expected | $ bet | Profit if held | z |\n"
               "|---|---:|---:|---:|---:|---:|---:|")
    for wl, r in rep.head(25).iterrows():
        out.append(f"| [{disp(r['name'], wl)}](https://polymarket.com/profile/{wl}) | "
                   f"{int(r.bets)} | {int(r.wins)} | {r.expected:.1f} | {usd(r.usd)} | "
                   f"{usd(r.pnl)} | {r.z:+.1f} |")
    out.append("")
    out.append("## Biggest winning late long shots\n")
    out.append("| Date | Trader | Market | Price | Bet | Hours before settlement | Profit |\n"
               "|---|---|---|---:|---:|---:|---:|")
    for r in p[p.won == 1].nlargest(30, "pnl").itertuples():
        out.append(f"| {r.first:%Y-%m-%d} | [{disp(r.name, r.wallet)}]"
                   f"(https://polymarket.com/profile/{r.wallet}) | "
                   f"{str(r.q).replace('|', '/')} | {r.price:.2f} | {usd(r.usd)} | "
                   f"{r.h:.0f} | {usd(r.pnl)} |")
    out.append("\nLimits: only trades of the size threshold or more are seen, so a bet built "
               "from many small fills is missed; \"hours before settlement\" uses the "
               "market's close time, which can lag the real-world event.")
    (REPORTS / "insider_screen.md").write_text("\n".join(out) + "\n")
    print(f"Wrote {REPORTS / 'insider_screen.md'} ({len(p):,} positions)")


if __name__ == "__main__":
    main()
