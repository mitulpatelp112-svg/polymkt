# How Polymarket's top 100 made money, Oct 2025 - Oct 2026

A summary of `strategies.md`, `insider_screen.md`, `big_trades.md` and
`top_traders.md`. All figures come from Polymarket's public APIs; they are
statistical patterns, not findings of wrongdoing, and no wallet here is
identified with a real person.

## The short answer

The top 100 made about **$326M**, and **92% of their bets were on sports**.
They fall into a few playbooks:

| Playbook | Wallets | Profit | What it is |
|---|---:|---:|---|
| A few huge bets | 27 | $112.6M | 2-30 positions, often $1-7M each, mostly 2026 World Cup matches, placed in the hour before kickoff |
| Directional, diversified | 37 | $80.2M | Hundreds of sports bets with a modest, real edge |
| High-frequency market making | 14 | $72.5M | Millions of tiny trades on both sides of sports markets; earns the spread and liquidity rewards |
| High-frequency directional | 13 | $45.9M | Bots taking one side, many markets |
| In-play trading | 6 | $9.7M | Most money placed after kickoff |
| Near-certainty harvesting | 2 | $3.3M | Buying 95c+ outcomes for small, steady returns |
| Parlays / combos | 1 | $2.3M | Multi-leg combo markets |

## Testing the suspicious explanations

### Fixed matches / sports insiders: not supported

The 27 "few huge bets" wallets look exactly like insiders: every one is less
than a year old, they placed most of their money minutes before kickoff, and
65% of their bets won.

But **21 of the 100 biggest losers have the identical pattern and lost $84M**,
winning only 38% of the time. And across **every $100K+ sports bet by anyone in
the 3 hours before kickoff (2,540 bets), there is no edge at all** (won 55.6%
at an average price of 0.57, z = -1.3). The wallets that try this most often
win about as often as the odds say; the busiest, `0x492442ea...`, placed 563
such bets worth $110M and would be down $8.7M holding them to the end.

Conclusion: the leaderboard's World Cup whales are mostly the lucky half of a
coin flip. Being on the top-100 list is itself selection for luck. This does
not clear any single wallet, but there is no market-wide trace of fixed games.

The timing (12-40 minutes before kickoff) also matches when official team
line-ups are published. Reacting to line-ups fast is a legitimate, public
edge and would produce the same timing.

### Real-world insiders (war, politics, policy): one event stands out

Across 9,051 non-sports markets, late long-shot bets (35c or less, final 72
hours, $5K+) won 26% at an average price of 16c. That edge is **entirely
explained by three markets**; without them it disappears (z = -0.5):

- **"US strikes Iran by February 28, 2026?"** - 47 wallets bought at 9-19c
  in the 72 hours before it resolved Yes. Some bought only hours before.
- "US strikes Iran by March 1, 2026?" and "Government shutdown on Saturday?"
- Separately, "Maduro out by January 31, 2026?" had a few buyers at 7-11c
  the day before.

Fresh accounts are a classic insider tell, but they don't separate these
buyers: 23% of them had accounts under a week old, about the same as the 26%
among long-shot buyers who lost. Many people pile into a geopolitical market
when the news heats up.

Verdict: **inconclusive, and the Iran strike market is the lead worth
pursuing.** Whether it looks like inside information depends on what was
public in those 72 hours (troop movements, official statements, press
reports), which this data cannot show. I have not checked the news timeline,
and you should not treat any named wallet as an insider on this evidence.

### Wash trading: not found

Top wallets often appear in the same settlement transactions, mostly the
market-making bots (RN1, swisstony, `0xd9e0aaca...`) that constantly quote the
same markets. The closest-linked pair, `0x8dxd` (wallet `0x63ce3421...`) and `k9Q2mX4L8A7ZP3R`, enter
the same crypto bets within 2 seconds a third of the time and share 663
transactions. But 555 of those 663 are on the *same* side; they traded
against each other for only about $8.5K. That looks like **one operator, or
one copied bot, running two wallets**, not wash trading. Counterparties
outside the top 100 can't be checked without on-chain data.

### In-play speed ("courtsiding"): small

Positions built after kickoff have a statistically real but small edge (52.3%
won at 0.51). The market makers do much of their trading in-play; their
edge is the spread, not knowing results early.

## When they bet

- The top 100 put about **$600M** into the final hour before kickoff, more
  than any other time window, and **$324M** in-play.
- The typical wallet's money lands **~94% of the way through a market's
  life**, a few hours before it settles. They bet late, when news is out.
- Edge by timing is small everywhere (1-4 points over the price); there is
  no "golden window" where top traders win far more than the odds.

## What I can't tell you

- Who is behind any wallet, or whether one person runs several.
- Whether the Iran strike buyers had non-public information.
- Strategies that need data this doesn't include: arbitrage against
  sportsbooks (comparing to bookmaker lines), on-chain fund flows between
  wallets, and resolution disputes (UMA votes).

## Getting trade data faster

- **Big trades market-wide:** `python -m polymkt.fetch_big_trades
  --min-cash 100000` scans each market's trades with a size filter (~30 min).
  The global feed only reaches back a few weeks.
- **One wallet's full history:** the `/activity` endpoint, walked a week at
  a time (`fetch_wallet_trades.py`).
- **Live:** Polymarket's public WebSocket streams trades as they happen;
  not used here.
- **On-chain:** a public Polygon node is reachable from here, and Goldsky's
  Edge API (needs a key) indexes Polymarket activity; the old public
  subgraphs are shut down.
