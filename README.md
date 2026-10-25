# polymkt

Tools for downloading and studying a year of [Polymarket](https://polymarket.com)
prediction-market data from its public, keyless APIs.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# 1. Metadata for every market ending on/after one year ago (resolved or still open)
#    ~4.9M markets / 1.2M events, ~15 min, ~1.5 GB
python -m polymkt.fetch_markets            # -> data/markets.parquet

# 2. Daily price history for markets with >= $100K volume (~95K markets, ~30 min, resumable)
python -m polymkt.fetch_prices             # -> data/prices.parquet
#    --min-volume 10000  ~420K markets, ~2 hours; reruns only fetch what's new

# 3. Report and charts
python -m polymkt.analyze                  # -> reports/summary.md + reports/*.png
```

`data/` is git-ignored; rerun the fetchers to rebuild it. Load it for your own
analysis with `pd.read_parquet("data/markets.parquet")`.

## Data

**`data/markets.parquet`**: one row per market. Key columns: `question`,
`event_title`, `tags` (pipe-separated, from the parent event), `startDate`,
`endDate`, `closedTime`, `closed`, `volumeNum` (lifetime USD volume),
`outcomes` / `outcome_prices` (JSON), `yes_token` (CLOB token id of the first
outcome) and `resolved_yes` (1 / 0 for resolved markets, empty otherwise).

**`data/prices.parquet`**: `market_id`, `t` (UTC), `p` (price of the first
outcome, 0 to 1).

## Caveats

- Volume is lifetime volume, not volume traded inside the window.
- About half of all markets never traded (auto-listed sports props); the API
  omits their volume and the analysis counts it as $0.
- `resolved_yes` is inferred from final outcome prices (1/0); voided or
  50/50 resolutions are left empty.
- Multi-outcome events (e.g. "Who will win X?") are stored as many binary
  markets, one per candidate.
- The APIs are undocumented in places and can change; field names here match
  what they returned in October 2026.
