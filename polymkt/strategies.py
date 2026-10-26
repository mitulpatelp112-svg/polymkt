"""How do the top wallets make money? Test strategy hypotheses on their trades.

Reads data/wallet_trades.parquet and data/loser_trades.parquet
(python -m polymkt.fetch_wallet_trades [--losers 100]), data/markets.parquet
and data/big_trades.parquet. Writes reports/strategies.md,
reports/wallet_profiles.csv and a chart.

The unit of analysis is a *position*: everything one wallet bought of one
outcome in one market (a single bet is often split into hundreds of fills).
The core measure is *edge*: a position's average price is the crowd's
probability that the outcome wins, so across positions, wins minus the sum of
prices, divided by its standard deviation, gives a z-score. Luck rarely gives
|z| > 3; skill, speed or information does. Different sources of edge leave
different fingerprints (when in a market's life the bet is placed, at what
price, in which category), and the biggest losers serve as a control group,
since a pattern among winners means little if losers share it. None of this
proves intent.

    python -m polymkt.strategies
"""
import json
from itertools import combinations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .analyze import BLUE, GRAY, MUTED, REPORTS, categorize, save, style, usd
from .fetch_markets import DATA

HOUR = 3600
LONGSHOT = 0.35       # average buy price at or below this counts as a long shot
NEAR_CERTAIN = 0.95   # at or above this counts as near-certain
WORLD_CUP = {"FIFA World Cup", "2026 FIFA World Cup"}

MARKET_COLS = ["conditionId", "question", "tags", "createdAt", "startDate", "endDate",
               "closedTime", "resolved_yes", "outcomes"]


def load_markets():
    m = pd.read_parquet(DATA / "markets.parquet", columns=MARKET_COLS)
    m = m.drop_duplicates("conditionId")
    m["category"] = m.tags.map(categorize)
    m["world_cup"] = m.tags.map(lambda s: bool(WORLD_CUP & set((s or "").split("|"))))
    m["binary"] = m.outcomes.map(lambda s: len(json.loads(s or "[]")) == 2)
    return m


def enrich(t, m):
    """Attach market data and timing to trades."""
    t = t.merge(m, on="conditionId", how="left")
    t["usd"] = t.usdcSize.fillna(t["size"] * t.price)
    ok = t.resolved_yes.notna() & t.binary.fillna(False) & t.outcomeIndex.isin([0, 1])
    t["won"] = np.where(ok, (t.outcomeIndex == 0) == (t.resolved_yes == 1), np.nan)
    opened = t.startDate.fillna(t.createdAt)
    closed = t.closedTime.fillna(t.endDate)
    t["h_after_open"] = (t.time - opened).dt.total_seconds() / HOUR
    t["h_to_close"] = (closed - t.time).dt.total_seconds() / HOUR
    t["h_to_end"] = (t.endDate - t.time).dt.total_seconds() / HOUR
    t["life_frac"] = ((t.time - opened) / (closed - opened)).clip(0, 1)
    # Combo (parlay) markets are not listed under events, so they have no metadata.
    t["combo"] = t.category.isna() & t.title.str.contains(" AND ", na=False)
    return t


def positions(t):
    """One row per wallet x market x outcome bought, timings dollar-weighted."""
    b = t[t.side == "BUY"].copy()
    for c in ("h_to_close", "h_to_end", "h_after_open", "life_frac"):
        b[c + "_w"] = b[c] * b.usd
    g = b.groupby(["wallet", "conditionId", "outcomeIndex"], sort=False)
    p = g.agg(usd=("usd", "sum"), shares=("size", "sum"), fills=("usd", "size"),
              won=("won", "first"), category=("category", "first"),
              world_cup=("world_cup", "first"), question=("question", "first"),
              title=("title", "first"), outcome=("outcome", "first"),
              first=("time", "min"), last=("time", "max"),
              h_to_close_w=("h_to_close_w", "sum"), h_to_end_w=("h_to_end_w", "sum"),
              h_after_open_w=("h_after_open_w", "sum"), life_frac_w=("life_frac_w", "sum"))
    p["price"] = p.usd / p.shares
    for c in ("h_to_close", "h_to_end", "h_after_open", "life_frac"):
        p[c] = p.pop(c + "_w") / p.usd
    p["pnl_if_held"] = np.where(p.won.notna(), p.shares * p.won - p.usd, np.nan)
    return p.reset_index()


def edge(p):
    """(positions, win rate, avg price, z) over resolved positions."""
    r = p[p.won.notna() & p.price.between(0.001, 0.999)]
    if len(r) == 0:
        return 0, np.nan, np.nan, np.nan
    var = (r.price * (1 - r.price)).sum()
    z = (r.won.sum() - r.price.sum()) / np.sqrt(var) if var > 0 else np.nan
    return len(r), r.won.mean(), r.price.mean(), z


def timing_bucket(h):
    return pd.cut(h, [-np.inf, 0, 1, 3, 24, 24 * 7, 24 * 30, np.inf],
                  labels=["after start / scheduled end", "0 - 1 h before", "1 - 3 h before",
                          "3 - 24 h before", "1 - 7 days before", "1 - 4 weeks before",
                          "> 4 weeks before"])


def profiles(t, pos, info, weeks):
    sampled = weeks.groupby("wallet").truncated.any()
    bios = weeks.dropna(subset=["bio"]).groupby("wallet").bio.first()
    rows = []
    for w, p in pos.groupby("wallet"):
        g = t[t.wallet == w]
        n, wr, avgp, z = edge(p)
        cat = p.groupby("category").usd.sum()
        bu = p.usd.sum()
        late_ls = p[(p.price <= LONGSHOT) & p.h_to_close.between(0, 48)]
        i = info.get(w, {})
        rows.append({
            "wallet": w, "name": i.get("name"), "profit": i.get("profit"),
            "trades_year": i.get("trades"), "first_seen": i.get("first_seen"),
            "sampled": bool(sampled.get(w, False)), "trades_fetched": len(g),
            "markets": g.conditionId.nunique(), "positions": len(p),
            "usd_bought": bu, "median_position": p.usd.median(), "max_position": p.usd.max(),
            "top5_share": p.usd.nlargest(5).sum() / bu if bu else np.nan,
            "top_category": cat.idxmax() if len(cat) else ("Combos" if g.combo.any() else None),
            "top_category_share": cat.max() / bu if len(cat) and bu else np.nan,
            "world_cup_share": p[p.world_cup.fillna(False)].usd.sum() / bu if bu else np.nan,
            "combo_share": g[g.combo].usd.sum() / g.usd.sum() if g.usd.sum() else np.nan,
            "h_to_close": np.average(p.h_to_close.fillna(0), weights=p.usd) if bu else np.nan,
            "h_to_start": p.h_to_end.median(),
            "life_frac": np.average(p.life_frac.fillna(0), weights=p.usd) if bu else np.nan,
            "share_after_start": p[p.h_to_end < 0].usd.sum() / bu if bu else np.nan,
            "share_near_certain": p[p.price >= NEAR_CERTAIN].usd.sum() / bu if bu else np.nan,
            "share_longshot": p[p.price <= LONGSHOT].usd.sum() / bu if bu else np.nan,
            "both_sides": (p.groupby("conditionId").outcomeIndex.nunique() >= 2).mean(),
            "sell_share": (g.side == "SELL").mean(),
            "resolved_positions": n, "win_rate": wr, "avg_price": avgp, "edge_z": z,
            "usd_win_rate": (p.won * p.usd).sum() / p[p.won.notna()].usd.sum()
            if p.won.notna().any() else np.nan,
            "late_longshots": len(late_ls), "late_longshot_wins": late_ls.won.sum(),
            "late_longshot_z": edge(late_ls)[3],
            "bio": bios.get(w),
        })
    p = pd.DataFrame(rows).sort_values("profit", ascending=False)
    p["style"] = p.apply(classify, axis=1)
    return p


def classify(r):
    """Transparent rule-based label; thresholds are printed in the report."""
    if r.combo_share >= 0.5:
        return "Parlays / combos"
    if r.trades_year >= 100_000 and r.both_sides >= 0.3:
        return "High-frequency market making"
    if r.share_near_certain >= 0.5:
        return "Near-certainty harvesting"
    if r.positions <= 30 and r.top5_share >= 0.6:
        return "A few huge bets"
    if r.share_after_start >= 0.3:
        return "In-play trading"
    if r.trades_year >= 100_000:
        return "High-frequency directional"
    return "Directional, diversified"


def shared_tx(t):
    """Pairs of wallets that appear in the same settlement transaction."""
    tx = t.dropna(subset=["transactionHash"]).groupby("transactionHash").wallet.unique()
    tx = tx[tx.map(len) >= 2]
    pairs = {}
    for ws in tx:
        for a, b in combinations(sorted(ws), 2):
            pairs[(a, b)] = pairs.get((a, b), 0) + 1
    return pd.Series(pairs, dtype=float).sort_values(ascending=False)


def copy_pairs(pos, min_markets=10):
    """Pairs that bought the same outcome in the same market within 10 minutes."""
    f = pos[["wallet", "conditionId", "outcomeIndex", "first"]]
    j = f.merge(f, on=["conditionId", "outcomeIndex"])
    j = j[(j.wallet_x < j.wallet_y)
          & ((j.first_x - j.first_y).abs() <= pd.Timedelta(minutes=10))]
    c = j.groupby(["wallet_x", "wallet_y"]).size().reset_index(name="same_bets")
    mk = f.groupby("wallet").conditionId.nunique()
    c["overlap"] = c.same_bets / np.minimum(c.wallet_x.map(mk), c.wallet_y.map(mk))
    return c[c.same_bets >= min_markets].sort_values("overlap", ascending=False)


def edge_rows(p, by, min_n=20):
    rows = []
    for k, d in p.groupby(by, observed=True):
        n, wr, avgp, z = edge(d)
        if n >= min_n:
            rows.append((k, n, wr, avgp, z, d.usd.sum()))
    return rows


def edge_table(out, rows, label):
    out.append(f"| {label} | Positions | Avg price | Win rate | Edge z | $ |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for k, n, wr, p, z, u in rows:
        out.append(f"| {k} | {n:,} | {p:.2f} | {wr:.1%} | {z:+.1f} | {usd(u)} |")
    out.append("")


def disp(name, wallet):
    return name if isinstance(name, str) and name and not name.startswith("0x") else wallet[:10] + "..."


def big_trade_positions(m):
    path = DATA / "big_trades.parquet"
    if not path.exists():
        return None, None
    bt = pd.read_parquet(path).drop(columns=["tags"], errors="ignore")
    bt = bt.rename(columns={"proxyWallet": "wallet"})
    names = m.set_index("conditionId").outcomes.map(lambda s: json.loads(s or "[]"))
    bt["outcomeIndex"] = [
        (names.get(c) or []).index(o) if o in (names.get(c) or []) else -1
        for c, o in zip(bt.conditionId, bt.outcome)]
    bt["usdcSize"] = bt.cash
    bt = enrich(bt, m)
    return bt, positions(bt)


def main():
    m = load_markets()
    t = enrich(pd.read_parquet(DATA / "wallet_trades.parquet"), m)
    weeks = pd.read_parquet(DATA / "wallet_weeks.parquet")
    top = pd.read_csv(REPORTS / "top_traders.csv")
    pnl = pd.read_parquet(DATA / "trader_pnl.parquet")
    info = {r.wallet: {"name": r.name, "profit": r.profit, "trades": r.trades,
                       "first_seen": r.first_seen} for r in pnl.itertuples()}
    pos = positions(t)
    prof = profiles(t, pos, info, weeks)
    prof.to_csv(REPORTS / "wallet_profiles.csv", index=False)

    lt_path = DATA / "loser_trades.parquet"
    if lt_path.exists():
        lt = enrich(pd.read_parquet(lt_path), m)
        lpos = positions(lt)
        lprof = profiles(lt, lpos, info, pd.read_parquet(DATA / "loser_weeks.parquet"))
    else:
        lt = lpos = lprof = None
    bt, bpos = big_trade_positions(m)

    n, wr, avgp, z = edge(pos)
    out = ["# How the top 100 make money: strategy and integrity tests\n",
           f"{len(t):,} trades by the 100 most profitable wallets of the past year, grouped "
           f"into {len(pos):,} positions (one wallet, one market, one outcome). "
           f"{int(prof.sampled.sum())} high-frequency wallets are sampled at up to 2,000 "
           "trades a week. **Edge z** compares how often bought outcomes won with how often "
           "their prices said they would; |z| > 3 is very unlikely to be luck. The 100 "
           "biggest losers are the control group. These are statistical patterns, not "
           "findings of wrongdoing.\n",
           "Timing terms: *before close* is time until the market was settled. *Before "
           "start* uses the market's scheduled end date, which for sports game markets is "
           "kickoff (they settle about 2 hours later), so negative means bought in-play.\n"]

    # --- Headline: what the top 100 do -------------------------------------------------
    s = prof.groupby("style").agg(wallets=("wallet", "size"), profit=("profit", "sum"))
    out += ["## How they make money: six playbooks\n",
            "Rule-based labels (`classify` in `polymkt/strategies.py`): parlays = half "
            "their volume in combo markets; market making = 100K+ trades a year and buying "
            "both outcomes in 30%+ of markets; near-certainty = half of dollars at 95c+; "
            "a few huge bets = 30 or fewer positions with 60%+ of dollars in the top five; "
            "in-play = 30%+ of dollars after kickoff / scheduled start.\n",
            "| Playbook | Wallets | Their profit | Median positions | Median win rate | "
            "Median avg price |", "|---|---:|---:|---:|---:|---:|"]
    for k, r in s.sort_values("profit", ascending=False).iterrows():
        d = prof[prof["style"] == k]
        out.append(f"| {k} | {int(r.wallets)} | {usd(r.profit)} | {d.positions.median():,.0f} | "
                   f"{d.win_rate.median():.0%} | {d.avg_price.median():.2f} |")
    out.append("")
    sports = pos[pos.category == "Sports"].usd.sum() / pos.usd.sum()
    wc = pos[pos.world_cup.fillna(False)].usd.sum() / pos.usd.sum()
    out.append(f"**{sports:.0%}** of the top 100's buy dollars went into sports, and "
               f"**{wc:.0%}** into 2026 World Cup markets alone. Across {n:,} resolved "
               f"positions they won **{wr:.1%}** at an average price of **{avgp:.2f}** "
               f"(edge z = **{z:+.1f}**).\n")

    # --- Timing ---------------------------------------------------------------------------
    out.append("## When they bet\n")
    out.append("Dollar-weighted, per position. Win rate vs. price shows whether bets placed "
               "at that moment beat the market.\n")
    pos["bucket"] = timing_bucket(pos.h_to_end)
    rows = edge_rows(pos, "bucket")
    b = pd.DataFrame(rows, columns=["k", "n", "wr", "p", "z", "usd"]).set_index("k")
    fig, ax = plt.subplots(figsize=(8, 3.8))
    ax.bar(b.index.astype(str), (b.wr - b.p) * 100, color=BLUE, width=0.7)
    ax.axhline(0, color=GRAY, lw=1)
    ax.set_ylabel("Win rate minus price (pts)", color=MUTED)
    style(ax, "Top-100 edge by when the bet was placed (before kickoff / scheduled end)")
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right", fontsize=8)
    out.append(save(fig, "edge_by_timing.png") + "\n")
    edge_table(out, rows, "Placed")
    med = prof[["h_to_close", "life_frac"]].median()
    out.append(f"The median wallet's dollar-weighted bet lands **{med.h_to_close:,.1f} hours** "
               f"before the market settles, **{med.life_frac:.0%}** of the way through the "
               "market's life: they bet late, when most information is already out.\n")

    out.append("## Edge by category\n")
    edge_table(out, sorted(edge_rows(pos, "category"), key=lambda r: -r[5]), "Category")

    # --- Hypotheses -----------------------------------------------------------------------
    out.append("## Testing the explanations\n")

    out.append("### A. \"They had inside information\" (fixed games, insiders)\n")
    few = prof[prof["style"] == "A few huge bets"]
    fp = pos[pos.wallet.isin(few.wallet)]
    start = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=365)
    new = (pd.to_datetime(few.first_seen, utc=True) >= start).mean()
    out.append(f"The case for: {len(few)} of the top 100 made their money from 30 or fewer "
               f"positions (together {usd(few.profit.sum())}). "
               f"{fp[fp.world_cup.fillna(False)].usd.sum() / fp.usd.sum():.0%} of their dollars "
               f"went on 2026 World Cup markets, the average dollar was bet "
               f"{np.average(fp.h_to_end.fillna(0), weights=fp.usd):.1f} hours before "
               f"kickoff, median position {usd(fp.usd.median())}, and {new:.0%} of these "
               f"wallets first traded within the past year; {edge(fp)[1]:.0%} of their resolved "
               "positions won at an average price near 50c. Fresh wallets, a few huge bets "
               "close to the event, mostly winners: the textbook insider shape.\n")
    if lprof is not None:
        lfew = lprof[lprof["style"] == "A few huge bets"]
        out.append(f"The control group: **{len(lfew)} of the 100 biggest losers** have the "
                   f"exact same shape and together lost **{usd(-lfew.profit.sum())}**. "
                   "Side by side:\n")
        out.append("| Group | Wallets | Positions | Win rate | Avg price | Edge z |\n"
                   "|---|---:|---:|---:|---:|---:|")
        for label, pr, ps in (("Winners, few huge bets", few, pos),
                              ("Losers, few huge bets", lfew, lpos)):
            n_, wr_, p_, z_ = edge(ps[ps.wallet.isin(pr.wallet)])
            out.append(f"| {label} | {len(pr)} | {n_:,} | {wr_:.1%} | {p_:.2f} | {z_:+.1f} |")
            # positions on one match are correlated, so these z-scores overstate certainty
        n_, wr_, p_, z_ = edge(pd.concat([pos[pos.wallet.isin(few.wallet)],
                                          lpos[lpos.wallet.isin(lfew.wallet)]]))
        out.append(f"| **Both together** | {len(few) + len(lfew)} | {n_:,} | {wr_:.1%} | "
                   f"{p_:.2f} | **{z_:+.1f}** |\n")
    if bpos is not None:
        pk = bpos[(bpos.category == "Sports") & bpos.h_to_end.between(0, 3)]
        n_, wr_, p_, z_ = edge(pk)
        floor = usd(round(bt.cash.min(), -4))
        out.append(f"The whole market: every {floor}+ sports bet by *anyone* "
                   f"placed in the 3 hours before kickoff - {n_:,} resolved positions - won "
                   f"{wr_:.1%} at an average price of {p_:.2f} (edge z = **{z_:+.1f}**).\n")
        rep = pk[pk.won.notna()].groupby("wallet").agg(
            bets=("won", "size"), wins=("won", "sum"), price=("price", "mean"),
            usd=("usd", "sum"), pnl=("pnl_if_held", "sum"))
        rep = rep[rep.bets >= 5].sort_values("bets", ascending=False)
        if len(rep):
            nm = dict(zip(bt.wallet, bt.name))
            out.append("Repeat pre-kickoff whales (5+ such bets): an insider should keep "
                       "winning; a gambler's record drifts back toward the odds.\n")
            out.append("| Trader | Bets | Won | Expected from prices | $ bet | Result if held |\n"
                       "|---|---:|---:|---:|---:|---:|")
            for w, r in rep.head(20).iterrows():
                out.append(f"| [{disp(nm.get(w), w)}](https://polymarket.com/profile/{w}) | "
                           f"{int(r.bets)} | {int(r.wins)} | {r.price * r.bets:.1f} | "
                           f"{usd(r.usd)} | {usd(r.pnl)} |")
            out.append("")
    if bpos is not None:
        out.append(("**Verdict: not supported.** " if z_ < 2 else "**Verdict: supported.** ")
                   + f"Big pre-kickoff sports money as a whole won {wr_:.1%} against "
                   f"{p_:.0%} implied (z = {z_:+.1f}). "
                   + ("The winners on the leaderboard are the lucky half of a coin flip; "
                      "the unlucky half is on the losers' list. "
                      "This doesn't clear any one wallet, but there is no market-wide "
                      "sign of fixed matches.\n" if z_ < 2 else
                      "Big late sports money beat the odds; dig into the repeat winners.\n"))

    out.append("### B. \"They know the result before the market does\" (in-play speed)\n")
    inplay = pos[pos.h_to_end < 0]
    n_, wr_, p_, z_ = edge(inplay)
    pre = pos[pos.h_to_end.between(0, 24)]
    n2, wr2, p2, z2 = edge(pre)
    out.append(f"Positions built after kickoff / scheduled start: {n_:,}, won {wr_:.1%} at "
               f"{p_:.2f} (z = {z_:+.1f}). For comparison, the last 24 hours before start: "
               f"{n2:,}, won {wr2:.1%} at {p2:.2f} (z = {z2:+.1f}). A strong in-play edge "
               "would point to faster data feeds or being at the venue (\"courtsiding\"), "
               "which traditional sportsbooks often restrict.\n")
    ip = prof[prof.share_after_start >= 0.3].sort_values("profit", ascending=False)
    if len(ip):
        out.append("| Trader | Profit | $ after start | Win rate | Edge z |\n|---|---:|---:|---:|---:|")
        for r in ip.head(10).itertuples():
            out.append(f"| {disp(r.name, r.wallet)} | {usd(r.profit)} | {r.share_after_start:.0%} "
                       f"| {r.win_rate:.1%} | {r.edge_z:+.1f} |")
        out.append("")

    out.append("### C. \"They're the house\" (market making)\n")
    mm = prof[prof["style"] == "High-frequency market making"]
    out.append(f"{len(mm)} wallets ({usd(mm.profit.sum())}) trade hundreds of thousands to "
               "millions of times a year in small sizes, buying both sides of the same "
               f"market (median {mm.both_sides.median():.0%} of markets). Their win rate sits "
               "near their average price: they are not predicting, they are earning the "
               "spread and Polymarket's liquidity rewards. Legitimate, and the steadiest "
               "money on the list.\n")

    out.append("### D. \"Picking up pennies\" (near-certain outcomes)\n")
    nc = prof[prof["style"] == "Near-certainty harvesting"]
    out.append(f"{len(nc)} wallets ({usd(nc.profit.sum())}) put most of their dollars on "
               "outcomes priced 95c+, often after the result is effectively known but before "
               "the market settles. Small, steady returns; the risk is a rare upset or a "
               "disputed resolution wiping out many wins.\n")

    out.append("### E. Self-dealing / wash trading\n")
    pairs = shared_tx(t)
    out.append(f"Pairs of top-100 wallets matched in the same settlement transaction: "
               f"{len(pairs):,}. Big traders in the same markets get matched against each "
               "other routinely; wash trading would look like the same pair matched "
               "constantly with no clear winner. "
               + ("The most frequent pairs:" if len(pairs) else "None found."))
    if len(pairs):
        nm = dict(zip(prof.wallet, prof.name))
        out.append("\n| Wallet A | Wallet B | Shared transactions |\n|---|---|---:|")
        for (a, b2), c in pairs.head(10).items():
            out.append(f"| {disp(nm.get(a), a)} | {disp(nm.get(b2), b2)} | {int(c):,} |")
    out.append("\nThe public feed doesn't show counterparties, so wash trading with wallets "
               "outside the top 100 can't be checked without on-chain data.\n")

    out.append("### F. Coordinated or copy-trading wallets\n")
    cp = copy_pairs(pos)
    out.append("Pairs that bought the same outcome in the same market within 10 minutes of "
               "each other in 10+ markets. High overlap means copying, a shared signal, or "
               "one person running several wallets.\n")
    if len(cp):
        nm = dict(zip(prof.wallet, prof.name))
        out.append("| Wallet A | Wallet B | Same bets | Overlap |\n|---|---|---:|---:|")
        for r in cp.head(15).itertuples():
            out.append(f"| {disp(nm.get(r.wallet_x), r.wallet_x)} | "
                       f"{disp(nm.get(r.wallet_y), r.wallet_y)} | {r.same_bets:,} | {r.overlap:.0%} |")
    else:
        out.append("None found.")
    out.append("")

    out.append("### G. Late long shots outside sports and crypto (real-world insiders)\n")
    out.append("Single fills of this size are almost never long shots (whales build those "
               "from many smaller fills), so this is tested separately in "
               "[insider_screen.md](insider_screen.md) using every $5K+ trade.\n")

    # --- Per wallet -------------------------------------------------------------------------
    out.append("## Wallet by wallet\n")
    out.append("| # | Trader | Profit | Playbook | Positions | Top-5 share | Main category | "
               "Hrs before settlement | % through market life | Win rate | Avg price | "
               "Edge z | New this year |")
    out.append("|---:|---|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---|")
    start = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=365)
    for i, r in enumerate(prof.itertuples(), 1):
        new = "yes" if pd.Timestamp(r.first_seen) >= start else ""
        cat = f"{r.top_category} {r.top_category_share:.0%}" if r.top_category_share == r.top_category_share else str(r.top_category)
        out.append(f"| {i} | [{disp(r.name, r.wallet)}](https://polymarket.com/profile/{r.wallet}) "
                   f"| {usd(r.profit)} | {r.style}{' *' if r.sampled else ''} | {r.positions:,} | "
                   f"{r.top5_share:.0%} | {cat} | {r.h_to_close:,.1f} | {r.life_frac:.0%} | "
                   f"{r.win_rate:.0%} | {r.avg_price:.2f} | {r.edge_z:+.1f} | {new} |")
    out.append("\n\\* sampled at 2,000 trades a week.\n")

    out.append("## What the 20 biggest winners bet on\n")
    for r in prof.head(20).itertuples():
        bio = (f" Bio: \"{r.bio.strip()[:200]}\"" if isinstance(r.bio, str) and r.bio.strip()
               else "")
        out.append(f"**{disp(r.name, r.wallet)}** - {usd(r.profit)}, {r.style.lower()}.{bio}\n")
        top5 = pos[pos.wallet == r.wallet].nlargest(5, "usd")
        out.append("| Market | Bought | $ | Avg price | Hrs before start | Result |\n"
                   "|---|---|---:|---:|---:|---|")
        for x in top5.itertuples():
            res = {1.0: "won", 0.0: "lost"}.get(x.won, "open / multi")
            out.append(f"| {str(x.question or x.title).replace('|', '/')} | {x.outcome} | "
                       f"{usd(x.usd)} | {x.price:.2f} | {x.h_to_end:,.1f} | {res} |")
        out.append("")

    out.append("## Limits\n")
    out.append("- Wins are scored assuming positions are held to settlement, and only in "
               "two-outcome markets.\n"
               "- Sampled wallets keep the earliest 2,000 trades of each week, which can tilt "
               "their timing toward the start of the week.\n"
               "- The control group is the 100 biggest losers, not a random sample; it is "
               "there to show the same strategies also lose, not to measure base rates.\n"
               "- Statistics cannot separate skill, speed and inside information on their "
               "own, and nothing here identifies the people behind wallets.")
    (REPORTS / "strategies.md").write_text("\n".join(out) + "\n")
    print(f"Wrote {REPORTS / 'strategies.md'}")


if __name__ == "__main__":
    main()
