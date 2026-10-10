# How to read the GEX heatmap

A plain-English guide for people and for AI agents (Muse) that write the daily
summary. It explains what the heatmap shows, what it may mean, what the
research supports, and how to write a short daily summary.

## 1. What you are looking at

- **Rows** are strike prices. SPY and QQQ rows also show the SPX or NDX level.
- **Columns** are expiration dates, nearest on the left (up to 60 days out; the daily images show
  the nearest 8).
- **The dashed row** is the current price (spot).
- **Each cell** is the gamma exposure (GEX) of all options at that strike and
  expiration, in US dollars per 1% move.

GEX is **not trading volume**. It is:

```text
gamma x open interest x 100 shares x spot^2 x 1%
```

In plain words: if the price moves 1%, how many dollars of stock would the
people who sold those options have to buy or sell to stay hedged. A cell of
$500M means about $500M of hedging per 1% move, from that one strike and date.

**Color:** the app counts call gamma as positive (green) and put gamma as
negative (red). Darker means bigger. "Gross" mode in the app shows total size
with no sign.

## 2. The big assumption

The colors only mean something if you assume **who is on the other side**.
The usual assumption (from the SqueezeMetrics GEX paper) is:

- investors **sell calls**, so market makers are **long calls** (long gamma), and
- investors **buy puts**, so market makers are **short puts** (short gamma).

Open interest does not show who is long or short. This assumption is often
roughly right for SPY and QQQ, but it can be wrong (for example, when many
investors buy calls). Treat every reading below as "if the assumption holds".

## 3. How to read it

| What you see | What it may mean |
|---|---|
| Big **green** strike | Market makers are long gamma there. They sell when price rises and buy when it falls. That slows price down near the strike. Above spot, it often acts like a ceiling. |
| Big **red** strike | Market makers are short gamma there. They sell when price falls and buy when it rises. That can speed moves up. Below spot, it is support that can break fast. |
| Spot sits in **mostly green** (net GEX near spot > 0) | Calmer, back-and-forth trading is more likely. Moves away from spot tend to be pulled back. |
| Spot sits in **mostly red** (net GEX near spot < 0) | Bigger, trend-like moves are more likely, in either direction. |
| **Call wall** / **put wall** | The strikes with the most call / put open interest in the nearest expiration. Watched as resistance / support. They are raw open interest, not GEX. |
| **Max pain** | The strike where all options together would pay out the least at expiration. Some traders watch it on expiration day. It is an accounting number, not a force. |

## 4. Time: near dates vs. far dates

Gamma per contract depends on time left. For an at-the-money option it grows
roughly like `1 / sqrt(days left)`: the same open interest has about **5x more
gamma at 1 day than at 25 days**. Very close to expiration, gamma is high only
at strikes right next to spot and almost zero elsewhere.

What this means for reading the heatmap:

- **Left columns matter most today.** They have the most gamma near spot.
- **A big cell on a far date** means a lot of open interest there, because far
  dates start with little gamma per contract. Its effect is small today and
  **grows as that date gets closer**, if spot stays near the strike.
- **Big monthly or quarterly expirations** (the third Friday of the month) often
  show as large columns. Before they expire, price may "pin" near big strikes.
  After they expire, that gamma disappears, and the support or ceiling it gave
  goes away. Moves can get bigger after a large expiration.
- **A big value tomorrow but nothing after it** means that level only matters
  until tomorrow's close.

## 5. Gamma flip and vanna exposure (VEX)

The script prints both for SPY and QQQ ([ADR-0005](ADR-0005-gamma-flip-and-vanna.md)).

**Environment and gamma flip.** The script adds up the GEX of every
contract, then repeats that as if price were higher or lower (-10% to +10%).

- **Positive gamma environment** (net GEX at spot above zero): hedging tends
  to damp moves. Expect more back-and-forth trading.
- **Negative gamma environment** (below zero): hedging tends to add to moves.
  Expect bigger, trend-like moves.
- **Gamma flip**: the nearest price where net GEX changes sign. On the image
  it is the orange dashed line. Usually the flip is below spot in a positive
  environment: if price falls through it, the market moves into negative
  gamma.
- **Flip very close to spot** (within about 0.5%): the regime is fragile. A
  small move can switch it.
- **No flip within ±10%**: the regime is unlikely to change in normal days.

**Vanna exposure (VEX).** Vanna is how much an option's delta changes when IV
changes. VEX adds it up across contracts, in dollars of hedging per 1 IV
point, with the same call-positive/put-negative sign.

- **VEX > 0**: if IV falls (often after an event, or in a calm rally), hedging
  **buys** stock; if IV jumps (often in a sell-off), hedging **sells**. This
  is the "vanna rally" idea when IV drops.
- **VEX < 0**: the reverse.
- VEX matters most around events (Fed, CPI, earnings) and into big
  expirations, when IV moves the most.

Limits: IV is held fixed per contract while price moves, so in a real
sell-off (where IV rises) the true flip can be different. Only 1-60 day
expirations are counted, so numbers will differ from vendor sites.

## 6. What the research says

| Claim | Evidence |
|---|---|
| Short-gamma hedging trades with the move and adds momentum | Strong. Baltussen, Da, Lammers, Martens, *Journal of Financial Economics* (2021): gamma hedging predicts the last 30 minutes of the day across 60+ futures markets. |
| Negative gamma goes with momentum, positive with reversal, more so in less liquid markets | Good. Barbon and Buraschi, "Gamma Fragility" (working paper, 2020-2021). |
| Price "pins" to strikes with big open interest on expiration day | Good. Ni, Pearson, Poteshman (2005): stocks close at strikes on expiration days more often than chance. Not every time. |
| 0DTE (same-day) options make the market unstable | Weak. Dim, Eraker, Vilkov (2024) found little evidence. (This app does not include 0DTE.) |
| Exact call wall / put wall levels hold | Weak. Popular with traders, little formal research. |
| "Zero-gamma flip" level, vanna and charm flows into expiration | Mostly vendor commentary, not peer-reviewed. The positive/negative gamma regime behind the flip is supported by the papers above. |

Bottom line: GEX tells you about the **likely size and style** of moves
(calm vs. fast). It does **not** tell you the direction.

## 7. Common mistakes

- Reading GEX as volume or as buy/sell orders.
- Treating a wall as a guaranteed turning point.
- Ignoring the date: a big level that expires tomorrow is gone the day after.
- Forgetting the snapshot is from the last close if you read it before the
  open, and that open interest is updated only once a day.
- Treating the gamma flip as an exact line. It is an estimate that moves
  every day.
- Calling it a trading signal. It is context, not advice.

## 8. Daily summary recipe (for Muse)

Read `exports/heatmap.md`, which `scripts/daily_heatmap.py` writes. It has the
facts and the heatmap as a table. Quote its numbers; do not read numbers off
the images. The images (`exports/heatmap-spy.png`, `exports/heatmap-qqq.png`)
are for the user: send them with the summary.

For each symbol (SPY, then QQQ):

1. **Regime:** say POSITIVE or NEGATIVE gamma, and where the gamma flip is
   (with its SPX/NDX level and distance from spot). If the flip is within
   about 0.5% of spot, say the regime is fragile.
2. **VEX:** one short line: what hedging does if IV falls or rises.
3. **Levels:** name the biggest positive strike above spot (possible ceiling)
   and the biggest negative strike below spot (possible fast-break support),
   with their SPX/NDX levels. Mention the call wall and put wall.
4. **Dates:** say which expiration is the largest. If it is not the nearest
   one, say its effect grows as it gets closer. If a big date expires this
   week, say levels may change after it.
5. **Caveat:** one short line saying this assumes the usual market-maker
   positioning and is not advice.

Template (keep it under about 150 words):

```text
GEX daily, <date> (data: <data line>)

SPY $<spot> (~SPX <level>): <POSITIVE / NEGATIVE> gamma, flip $<flip> (~SPX <level>, <pct>)<, fragile if close>.
VEX <value>: if IV falls, hedging <buys / sells>; if IV rises, the opposite.
Ceiling to watch $<strike> (~SPX <level>); fast-break support $<strike> (~SPX <level>).
Walls (nearest expiration): call $<strike>, put $<strike>.
Biggest date: <expiration> (<n>d) <value> - <grows as it nears / expires this week>.

QQQ $<spot> (~NDX <level>): <same lines>

Assumes the usual market-maker positioning. Research only. Not financial advice.
```

If the output says "SYNTHETIC FIXTURE DATA", say so and do not summarize it as
real data.

## Sources

- SqueezeMetrics, "Gamma Exposure" white paper (the source of the call-positive,
  put-negative assumption):
  [PDF](https://squeezemetrics.com/monitor/download/pdf/white_paper.pdf)
- Baltussen, Da, Lammers, Martens, "Hedging Demand and Market Intraday
  Momentum", *Journal of Financial Economics* 142 (2021):
  [PDF](https://www3.nd.edu/~zda/intramom.pdf)
- Barbon, Buraschi, "Gamma Fragility":
  [paper page](https://abarbon.com/papers/gamma-fragility)
- Ni, Pearson, Poteshman, "Stock Price Clustering on Option Expiration Dates",
  *Journal of Financial Economics* (2005):
  [summary](https://scholars.hkbu.edu.hk/en/publications/stock-price-clustering-on-option-expiration-dates-3/)
- Avellaneda, Kasyan, Lipkin, stock pinning model:
  [slides](https://math.nyu.edu/inmemoriam/avellaneda/PinningSlides.pdf)
- Dim, Eraker, Vilkov, "0DTEs: Trading, Gamma Risk and Volatility Propagation"
  (2024): [review](https://harbourfrontquant.substack.com/p/the-rise-of-0dte-options-cause-for)
