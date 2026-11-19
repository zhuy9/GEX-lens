# How to Read the GEX Heatmap

A plain-English reference for both **people** and **AI agents (Muse)** writing the daily summary.

## Quick Reference

- **Rows:** Strike prices (with approximate `SPX`/`NDX` equivalents on `SPY`/`QQQ`).
- **Columns:** Expiration dates from left (nearest) to right (`1–30` DTE).
- **Colors:** Green = positive/call gamma (damping); red = negative/put gamma (amplifying); darker = larger dollar exposure.
- **Markers:** Dashed lines for `spot` (blue), `gamma flip` (orange), nearest-expiry `call wall` (green), and `put wall` (red). Out-of-window levels appear as `↑` / `↓` edge labels with `%` distance from spot.
- **Core rule:** GEX describes **expected move size/character (calm vs. fast)**, **never price direction**.

---

## 1. What Each Cell Measures

```text
GEX = gamma × open interest × 100 shares × spot² × 1%
```

- **Not trading volume:** Each cell is the estimated stock dollars that option dealers must buy or sell per `1%` spot move to stay delta-hedged.
- **Example:** A `$500M` cell means roughly `$500M` of hedging flow per `1%` move from that strike and expiration.
- **Sign convention:** Calls are counted `+` (green) and puts `-` (red). **Gross** mode in the web app sums absolute magnitudes without signs.

---

## 2. The Positioning Assumption

GEX signs depend on **who holds the other side of open interest**. Following the SqueezeMetrics convention:

- **Calls:** Assumed investors **sell** calls → market makers are **long** calls (`+` gamma).
- **Puts:** Assumed investors **buy** puts → market makers are **short** puts (`-` gamma).

Open interest alone does not prove who is long or short. This assumption works reasonably well on index ETFs (`SPY`, `QQQ`) most of the time, but can break down (e.g. heavy retail call-buying). Treat every interpretation below as conditional on this assumption holding.

---

## 3. Reading Levels & Colors

| Pattern | Typical Interpretation (if assumption holds) |
|---|---|
| Large **green** strike | Dealers are long gamma: they sell rallies and buy dips, damping moves. Above spot, often acts like resistance/a ceiling. |
| Large **red** strike | Dealers are short gamma: they buy rallies and sell dips, amplifying moves. Below spot, support that can accelerate quickly if broken. |
| Spot surrounded by **green** (net GEX > 0) | Mean-reverting, range-bound tape is more likely. |
| Spot surrounded by **red** (net GEX < 0) | Larger, trend-like moves in either direction are more likely. |
| **Call wall / Put wall** | Highest call / put **open interest** strike in the nearest expiration (raw OI, not GEX). Commonly watched as magnet/resistance or support. |
| **Max pain** | Strike where total option payout at expiry is minimized. An accounting point, not a market force. |

---

## 4. Time Decay: Near Dates vs. Far Dates

At-the-money gamma scales roughly as `1 / sqrt(days left)`:

- **Near expirations dominate today:** The same open interest carries **~5× more gamma at 1 DTE than at 25 DTE**, concentrated tightly around spot.
- **Far-date clusters are latent:** A large cell weeks out signals heavy open interest whose hedging impact is small today but **grows as expiration approaches** (if spot stays near it).
- **Monthly/quarterly expirations (3rd Friday):** Often hold the largest columns. Price may "pin" near big strikes before expiry, then move more freely once that gamma rolls off.
- **Expiring levels:** A big strike expiring tomorrow disappears at tomorrow's close.

---

## 5. Gamma Flip & Vanna Exposure (VEX)

Both are computed across `1–30` DTE contracts ([ADR-0005](ADR-0005-gamma-flip-and-vanna.md)):

- **Environment & Gamma Flip:**
  - Re-sums total net GEX across a `-10%` to `+10%` spot scan (holding each contract's IV fixed).
  - **Positive gamma** (`net GEX at spot > 0`): hedging damps volatility; **negative gamma** (`< 0`): hedging amplifies moves.
  - **Gamma flip:** Nearest spot level where net GEX crosses zero (orange dashed line / edge label).
  - **Flip within `~0.5%` of spot:** Regime is fragile — a small move can flip hedging behavior.
  - **No flip within `±10%`:** Regime is unlikely to change during normal sessions.
- **Vanna Exposure (VEX):**
  - Measures delta-hedging dollars triggered per `+1` IV point change (call-positive, put-negative).
  - **VEX > 0:** Falling IV (post-event crush, calm drift up) **buys** stock; rising IV (sell-off) **sells** stock.
  - **VEX < 0:** Opposite flows.
  - Most relevant around scheduled catalysts (FOMC, CPI, earnings) and large expirations.
- **Limits:** Sticky-strike IV understates flip shifts during sharp sell-offs (when IV spikes), and `1–30` DTE totals will differ from full-chain vendor numbers.

---

## 6. What the Research Supports

| Claim | Evidence Strength |
|---|---|
| Short-gamma hedging trades with the move and adds intraday momentum | **Strong** — Baltussen, Da, Lammers, Martens (*JFE*, 2021): gamma hedging predicts last-30-min returns across 60+ futures markets. |
| Negative gamma correlates with momentum; positive with mean reversion | **Good** — Barbon & Buraschi, *"Gamma Fragility"* (2020–2021). |
| Stocks pin near high-OI strikes on expiration day | **Good** — Ni, Pearson, Poteshman (*JFE*, 2005): statistically above chance, though not guaranteed. |
| 0DTE options destabilize the broader market | **Weak** — Dim, Eraker, Vilkov (2024) found little evidence. *(This app excludes 0DTE.)* |
| Exact call/put wall levels hold as hard barriers | **Weak** — Popular trader heuristic, little formal support. |
| Exact zero-gamma flip, vanna, and charm pinpoints | **Practitioner rule of thumb** — Underlying positive/negative regime is supported; exact flip/VEX levels are mostly vendor commentary. |

---

## 7. Common Mistakes to Avoid

- Treating GEX as trading volume or directional order flow.
- Treating call/put walls or gamma flip as exact, guaranteed turning points.
- Ignoring expiration dates (tomorrow's big level vanishes after tomorrow's close).
- Forgetting pre-open timing (before 09:30 ET, prices are prior close; OI is overnight).
- Calling any GEX reading a buy/sell signal.

---

## 8. Daily Summary Recipe (for Muse)

- **Source of truth:** Read `exports/heatmap.md` (written by `scripts/daily_heatmap.py`). Always quote its exact numbers — never read numbers off the PNGs.
- **Attachments:** Send `exports/heatmap-spy.png` and `exports/heatmap-qqq.png` alongside the summary.
- **Synthetic check:** If `exports/heatmap.md` says `SYNTHETIC FIXTURE DATA`, state clearly that it is test data and do not present it as live market data.

**Checklist for each symbol (`SPY`, then `QQQ`):**

1. **Regime:** State `POSITIVE` or `NEGATIVE` gamma and the gamma flip (`$`, `~SPX`/`~NDX`, `%` from spot). Flag as *fragile* if flip is within `~0.5%` of spot.
2. **VEX:** One line on whether falling/rising IV triggers hedging buys or sells.
3. **Key strikes & walls:** Name the largest positive strike above spot, largest negative strike below spot (with index equivalents), and nearest-expiry call/put walls.
4. **Expirations:** Name the largest expiration by gross GEX; note if it expires this week or grows as it nears.
5. **Caveat:** One short line noting the positioning assumption and that it is research only.

**Template (target `< 150` words):**

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

---

## Sources

- SqueezeMetrics, *"Gamma Exposure"* white paper: [PDF](https://squeezemetrics.com/monitor/download/pdf/white_paper.pdf)
- Baltussen, Da, Lammers, Martens, *"Hedging Demand and Market Intraday Momentum"*, *Journal of Financial Economics* 142 (2021): [PDF](https://www3.nd.edu/~zda/intramom.pdf)
- Barbon & Buraschi, *"Gamma Fragility"*: [paper page](https://abarbon.com/papers/gamma-fragility)
- Ni, Pearson, Poteshman, *"Stock Price Clustering on Option Expiration Dates"*, *JFE* (2005): [summary](https://scholars.hkbu.edu.hk/en/publications/stock-price-clustering-on-option-expiration-dates-3/)
- Avellaneda, Kasyan, Lipkin, stock pinning model: [slides](https://math.nyu.edu/inmemoriam/avellaneda/PinningSlides.pdf)
- Dim, Eraker, Vilkov, *"0DTEs: Trading, Gamma Risk and Volatility Propagation"* (2024): [review](https://harbourfrontquant.substack.com/p/the-rise-of-0dte-options-cause-for)
