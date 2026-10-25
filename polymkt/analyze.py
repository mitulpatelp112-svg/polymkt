"""Summarize a year of Polymarket data into reports/summary.md plus PNG charts.

Reads data/markets.parquet (required) and data/prices.parquet (optional; needed
for the calibration section).

    python -m polymkt.analyze
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .fetch_markets import DATA

REPORTS = Path(__file__).resolve().parent.parent / "reports"

# Colors from a CVD-validated reference palette: one series hue, muted ink.
BLUE, GRAY, INK, MUTED, GRID = "#2a78d6", "#8a8984", "#0b0b0b", "#52514e", "#e6e5e1"

# First matching tag wins, so more specific categories come first. Broad
# tags like "Politics" and "Trump" sit on Fed and war markets too, so
# Politics is checked late.
CATEGORIES = [
    ("Crypto", {"Crypto", "Bitcoin", "Ethereum", "Solana", "XRP", "Crypto Prices"}),
    ("Sports", {"Sports", "NBA", "NFL", "MLB", "NHL", "Soccer", "Tennis", "UFC", "Golf",
                "F1", "Cricket", "Esports", "EPL", "Champions League", "NCAA", "Basketball",
                "Football", "Baseball", "Hockey", "Boxing", "Games"}),
    ("Economy & Finance", {"Economy", "Finance", "Fed", "Fed Rates", "Stocks", "Business",
                           "Inflation", "Equities", "Commodities", "IPOs"}),
    ("Geopolitics", {"Geopolitics", "World", "Ukraine", "Russia", "Israel", "Middle East",
                     "China", "Iran", "Gaza"}),
    ("Elections", {"Elections", "Global Elections", "World Elections", "US Election",
                   "Midterms", "Primaries"}),
    ("Politics", {"Politics", "U.S. Politics", "Trump", "Trump Presidency", "Congress"}),
    ("Tech & AI", {"Tech", "AI", "Science", "Big Tech", "OpenAI"}),
    ("Culture", {"Culture", "Pop Culture", "Movies", "Music", "Awards", "Celebrities",
                 "Entertainment", "Mentions", "Tweet Markets"}),
    ("Weather", {"Weather", "Climate", "Temperature"}),
]


def categorize(tags):
    tagset = set(tags.split("|")) if tags else set()
    for name, keys in CATEGORIES:
        if tagset & keys:
            return name
    return "Other"


def style(ax, title, grid="y"):
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=10)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    if grid:
        ax.grid(axis=grid, color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def save(fig, name):
    fig.tight_layout()
    fig.savefig(REPORTS / name, dpi=150, facecolor="#fcfcfb")
    plt.close(fig)
    return f"![{name}]({name})"


def hbar(series, title, fmt, name):
    s = series.sort_values()
    fig, ax = plt.subplots(figsize=(8, 0.38 * len(s) + 1))
    ax.barh(s.index, s.values, color=BLUE, height=0.7)
    for y, v in enumerate(s.values):
        ax.text(v, y, " " + fmt(v), va="center", fontsize=8, color=MUTED)
    ax.set_xticks([])
    style(ax, title, grid=None)
    return save(fig, name)


def usd(v):
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:,.1f}{suf}"
    return f"${v:,.0f}"


def price_before(prices, markets, days):
    """Last traded price at least `days` before each resolved market closed."""
    m = markets[["id", "closedTime", "resolved_yes", "category"]].copy()
    m["id"] = m["id"].astype(str)
    m["cutoff"] = m["closedTime"] - pd.Timedelta(days=days)
    j = prices.merge(m, left_on="market_id", right_on="id")
    j = j[j["t"] <= j["cutoff"]].sort_values("t")
    return j.groupby("market_id").tail(1)[["market_id", "p", "resolved_yes", "category"]]


def calibration_section(markets, out):
    path = DATA / "prices.parquet"
    if not path.exists():
        out.append("_No price data yet - run `python -m polymkt.fetch_prices` for calibration._\n")
        return
    prices = pd.read_parquet(path)
    res = markets[markets.resolved_yes.notna() & markets.closedTime.notna()
                  & markets.id.astype(str).isin(set(prices.market_id))]
    out.append("## How accurate were the prices?\n")
    out.append(f"Resolved markets with price history: **{len(res):,}**. For each one we take "
               "the last daily price of the first outcome (usually \"Yes\") 1, 7 and 30 days "
               "before close, then compare with how it resolved. A well-calibrated market "
               "resolves Yes about 70% of the time when priced at 70c.\n")
    bins = np.linspace(0, 1, 11)
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], color=GRAY, lw=1, ls="--", label="Perfect calibration")
    rows, by_cat = [], None
    for days, alpha in ((1, 1.0), (7, 0.65), (30, 0.35)):
        snap = price_before(prices, res, days)
        if snap.empty:
            continue
        brier = ((snap.p - snap.resolved_yes) ** 2).mean()
        acc = ((snap.p >= 0.5) == (snap.resolved_yes == 1)).mean()
        rows.append(f"| {days} day{'s' if days > 1 else ''} before | {len(snap):,} | "
                    f"{brier:.3f} | {acc:.1%} |")
        if days == 1:
            by_cat = snap.assign(sq=(snap.p - snap.resolved_yes) ** 2,
                                 fav=(snap.p >= 0.5) == (snap.resolved_yes == 1)
                                 ).groupby("category").agg(n=("p", "size"), brier=("sq", "mean"),
                                                           fav=("fav", "mean"))
        snap["bin"] = pd.cut(snap.p, bins, include_lowest=True)
        g = snap.groupby("bin", observed=True).agg(p=("p", "mean"), y=("resolved_yes", "mean"),
                                                   n=("p", "size"))
        g = g[g.n >= 20]
        ax.plot(g.p, g.y, color=BLUE, alpha=alpha, lw=2, marker="o", ms=5,
                label=f"{days}d before close")
    ax.set_xlabel("Market price", color=MUTED)
    ax.set_ylabel("Share that resolved Yes", color=MUTED)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    style(ax, "Calibration: price vs. outcome", grid="both")
    ax.legend(frameon=False, fontsize=9, labelcolor=MUTED)
    out.append("| Snapshot | Markets | Brier score (lower is better) | Favorite won |")
    out.append("|---|---:|---:|---:|")
    out += rows
    out.append("\n" + save(fig, "calibration.png") + "\n")
    if by_cat is not None:
        out.append("By category, 1 day before close:\n")
        out.append("| Category | Markets | Brier score | Favorite won |\n|---|---:|---:|---:|")
        for name, r in by_cat.sort_values("n", ascending=False).iterrows():
            out.append(f"| {name} | {int(r.n):,} | {r.brier:.3f} | {r.fav:.1%} |")
        out.append("")
    out.append("How to read this: bars are daily, and the snapshot is the last bar at least "
               "N days before the market closed. For short markets (most sports games, "
               "daily crypto) that is a price from before the game or window even started, "
               "so the 1-day score is mostly a measure of pre-event forecasts, not of how "
               "close prices get to the answer. Long-dated markets - mostly \"Will X happen "
               "by date?\" questions that resolve No and sit at low prices - dominate the "
               "7- and 30-day snapshots, which is why those scores look better. Compare "
               "categories rather than snapshots. For finer timing, refetch with "
               "`--fidelity 60`.\n")


def main():
    REPORTS.mkdir(exist_ok=True)
    m = pd.read_parquet(DATA / "markets.parquet")
    m["category"] = m.tags.map(categorize)
    # The API omits volume for markets that never traded (mostly auto-listed
    # sports props); spot checks against the trades API find no trades.
    m["volumeNum"] = m.volumeNum.fillna(0)
    now = pd.Timestamp.now(tz="UTC")
    start = m.endDate.min()
    out = ["# Polymarket: one year in data\n",
           f"Generated {now:%Y-%m-%d} from Polymarket's public Gamma and CLOB APIs. "
           "Covers every market whose end date falls on or after "
           f"{(now - pd.Timedelta(days=365)):%Y-%m-%d} (resolved during the year, or still open).\n",
           "Volume figures are each market's lifetime traded volume as reported by "
           "Polymarket, so a market that opened before the window counts its earlier "
           "volume too.\n",
           "## Overview\n",
           "| Metric | Value |", "|---|---:|",
           f"| Markets | {len(m):,} |",
           f"| Events (groups of related markets) | {m.event_id.nunique():,} |",
           f"| Markets that ever traded | {(m.volumeNum > 0).sum():,} |",
           f"| Closed | {m.closed.sum():,} |",
           f"| Resolved Yes / No (first outcome) | {(m.resolved_yes == 1).sum():,} / "
           f"{(m.resolved_yes == 0).sum():,} |",
           f"| Still open | {(~m.closed.astype(bool)).sum():,} |",
           f"| Total lifetime volume | {usd(m.volumeNum.sum())} |",
           f"| Median volume per market | {usd(m.volumeNum.median())} |",
           f"| Markets with >= $10K volume | {(m.volumeNum >= 1e4).sum():,} |",
           f"| Markets with >= $1M volume | {(m.volumeNum >= 1e6).sum():,} |", ""]

    top_share = m.volumeNum.nlargest(max(1, len(m) // 100)).sum() / m.volumeNum.sum()
    out.append(f"Volume is highly concentrated: the top 1% of markets account for "
               f"**{top_share:.0%}** of all volume.\n")

    out.append("## By category\n")
    out.append("Categories are derived from each event's tags (first match wins; see "
               "`CATEGORIES` in `polymkt/analyze.py`).\n")
    cat = m.groupby("category").agg(markets=("id", "size"), volume=("volumeNum", "sum"))
    cat = cat.sort_values("volume", ascending=False)
    out.append(hbar(cat.volume, "Lifetime volume by category", usd, "volume_by_category.png"))
    out.append("\n| Category | Markets | Volume | Share of volume |\n|---|---:|---:|---:|")
    for name, r in cat.iterrows():
        out.append(f"| {name} | {int(r.markets):,} | {usd(r.volume)} | {r.volume / cat.volume.sum():.1%} |")
    out.append("")

    out.append("## Over time\n")
    month = m[m.endDate <= now].set_index("endDate").resample("MS").agg(
        {"id": "size", "volumeNum": "sum"})
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.bar(month.index.strftime("%b %y"), month.volumeNum, color=BLUE, width=0.7)
    ax.yaxis.set_major_formatter(lambda v, _: usd(v))
    style(ax, "Volume of markets ending each month")
    out.append(save(fig, "volume_by_month.png") + "\n")
    out.append("| Month | Markets ending | Their volume |\n|---|---:|---:|")
    for d, r in month.iterrows():
        out.append(f"| {d:%Y-%m} | {int(r.id):,} | {usd(r.volumeNum)} |")
    out.append("")

    dur = (m.endDate - m.startDate).dt.total_seconds() / 86400
    bands = pd.cut(dur, [-np.inf, 1 / 24, 1, 7, 30, 180, np.inf],
                   labels=["< 1 hour", "1 hour - 1 day", "1 - 7 days", "1 - 4 weeks",
                           "1 - 6 months", "> 6 months"])
    d = m.groupby(bands, observed=False).agg(markets=("id", "size"), volume=("volumeNum", "sum"))
    out.append("## Market lifespan (start to scheduled end)\n")
    out.append("| Lifespan | Markets | Volume |\n|---|---:|---:|")
    for name, r in d.iterrows():
        out.append(f"| {name} | {int(r.markets):,} | {usd(r.volume)} |")
    out.append("")

    out.append("## Biggest markets\n")
    out.append("| # | Market | Category | Volume | Status |\n|---:|---|---|---:|---|")
    for i, r in enumerate(m.nlargest(25, "volumeNum").itertuples(), 1):
        status = ("Resolved Yes" if r.resolved_yes == 1 else "Resolved No" if r.resolved_yes == 0
                  else "Closed" if r.closed else "Open")
        q = str(r.question).replace("|", "/")
        out.append(f"| {i} | [{q}](https://polymarket.com/event/{r.event_slug}) | "
                   f"{r.category} | {usd(r.volumeNum)} | {status} |")
    out.append("")

    calibration_section(m, out)

    (REPORTS / "summary.md").write_text("\n".join(out) + "\n")
    print(f"Wrote {REPORTS / 'summary.md'}")


if __name__ == "__main__":
    main()
