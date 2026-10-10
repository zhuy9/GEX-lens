"""Fetch SPY + QQQ and write one screenshot-ready GEX heatmap page (ADR-0004).

    cd backend && pip install -r requirements.txt
    python ../scripts/daily_heatmap.py            # live Nasdaq data
    python ../scripts/daily_heatmap.py --fixture  # synthetic data, no network

Writes exports/heatmap.html (git-ignored) and prints its path plus a short
text summary. Uses the backend's own refresh code with its own temporary
settings and database, so it never touches backend/settings.json or your
saved snapshots. Dividends come from reference/dividend_schedules.json and
the rate from the New York Fed, so no local reference_inputs.json is needed.
"""

import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app  # noqa: E402
import storage  # noqa: E402
from models import Settings  # noqa: E402

SYMBOLS = ("SPY", "QQQ")
OUT = ROOT / "exports/heatmap.html"
STRIKES_SHOWN = 17  # same spot-centered window as the app
EXPIRATIONS_SHOWN = 8  # keeps the page one screen wide
NY = ZoneInfo("America/New_York")
# Same colors as frontend/src/components/GexHeatmap.tsx.
NEG, POS, NEUTRAL = (220, 38, 38), (22, 163, 74), (250, 250, 250)
LEVELS = (("Call wall", "call_wall_strike"), ("Put wall", "put_wall_strike"), ("Max pain", "max_pain_strike"))


def fetch(fixture: bool) -> dict[str, dict]:
    tmp = Path(tempfile.mkdtemp())
    empty_inputs = {"input_schema_version": 1, "manual_rate": None, "schedules": {}}
    (tmp / "reference_inputs.json").write_text(json.dumps(empty_inputs))
    source = "fixture" if fixture else "manual_schedule"
    settings = Settings(
        source_mode="fixture" if fixture else "nasdaq",
        db_path=str(tmp / "heatmap.duckdb"),
        refresh_min_interval_seconds=60,
        pricing_model="cash_pv_bsm_v2",
        rate_source="fixture" if fixture else "nyfed_sofr",
        dividend_sources={"SPY": source, "QQQ": source, "AAPL": source},
        reference_inputs_path=str(tmp / "reference_inputs.json"),
    )
    storage.init_schema(settings.db_path)
    provider = app.build_provider(settings)
    rate_provider = app.build_rate_provider(settings.rate_source)
    dividend_providers = {s: app.build_dividend_provider(v) for s, v in settings.dividend_sources.items()}
    try:
        return {
            symbol: app._collect_and_save(
                settings,
                provider,
                rate_provider,
                dividend_providers,
                symbol,
                attempt_started_at=datetime.now(UTC),
                force_reference_refresh=False,
            )
            for symbol in SYMBOLS
        }
    finally:
        for p in (provider, rate_provider, *dividend_providers.values()):
            if close := getattr(p, "close", None):
                close()


def money(value: float) -> str:
    sign, v = ("-" if value < 0 else ""), abs(value)
    if v >= 1e6:
        return f"{sign}${v / 1e6:.1f}M"
    return f"{sign}${v / 1e3:.0f}K" if v >= 1e3 else f"{sign}${v:.0f}"


def strike_label(strike: str) -> str:
    n = float(strike)
    return f"${n:.0f}" if n.is_integer() else f"${n:.2f}"


def ny_time(iso: str | None) -> str:
    if not iso:
        return "unknown"
    return datetime.fromisoformat(iso).astimezone(NY).strftime("%b %d, %Y %I:%M %p ET")


def as_of(d: dict) -> str:
    # Nasdaq usually sends only a price date, no exact chain time.
    if d.get("chain_asof"):
        return f"chain as of {ny_time(d['chain_asof'])}"
    return f"price date {d.get('spot_asof_date') or 'unknown'} · fetched {ny_time(d['collected_at'])}"


def section(d: dict, conversion: dict | None) -> str:
    gex, spot = d["gex"], d["spot"]
    strikes, expirations = gex["strikes"], gex["expirations"][:EXPIRATIONS_SHOWN]
    nearest = min(range(len(strikes)), key=lambda i: abs(float(strikes[i]) - spot))
    start = max(0, min(nearest - STRIKES_SHOWN // 2, len(strikes) - STRIKES_SHOWN))
    rows = list(range(start, min(len(strikes), start + STRIKES_SHOWN)))[::-1]  # highest first
    values = [
        cell["signed_proxy"]
        for e in range(len(expirations))
        for s in rows
        if (cell := gex["cells"][e][s]) and cell["signed_proxy"] is not None
    ]
    bound = max(map(abs, values), default=1) or 1
    ratio = conversion["ratio"] if conversion else None
    index = f" · {conversion['index']} ≈ {spot * ratio:,.2f}" if ratio else ""

    def td(cell: dict | None) -> str:
        if not cell or cell["signed_proxy"] is None:
            return "<td></td>"
        v = cell["signed_proxy"]
        t = min(1, abs(v) / bound)
        target = POS if v >= 0 else NEG
        rgb = ",".join(str(round(a + (b - a) * t)) for a, b in zip(NEUTRAL, target, strict=True))
        color = "#fff" if t > 0.45 else "#18181b"
        return f'<td style="background:rgb({rgb});color:{color}">{money(v)}</td>'

    spot_row = f'<tr class="spot"><td colspan="{len(expirations) + 1}">Spot ${spot:.2f}{index}</td></tr>'
    body, spot_done = [], False
    for s in rows:
        if not spot_done and float(strikes[s]) < spot:
            body.append(spot_row)
            spot_done = True
        label = strike_label(strikes[s])
        if ratio:
            label += f" <span>· {round(float(strikes[s]) * ratio):,}</span>"
        cells = "".join(td(gex["cells"][e][s]) for e in range(len(expirations)))
        body.append(f"<tr><th>{label}</th>{cells}</tr>")
    if not spot_done:
        body.append(spot_row)

    dte = {p["expiration"]: p["dte"] for p in d["positioning"]}
    head = "".join(
        f"<th>{datetime.fromisoformat(e).strftime('%b %d')}<br><span>{dte.get(e, '?')}d</span></th>"
        for e in expirations
    )
    p0 = d["positioning"][0] if d["positioning"] else {}
    levels = " · ".join(
        f"{name} {strike_label(p0[key])}" if p0.get(key) else f"{name} n/a" for name, key in LEVELS
    )
    index_col = f" · {conversion['index']}" if conversion else ""
    return f"""
<section id="{d["symbol"].lower()}">
  <h2>{d["symbol"]} <small>spot ${spot:.2f}{index} · {as_of(d)}</small></h2>
  <p>Nearest expiration ({p0.get("expiration", "n/a")}): {levels}</p>
  <table><thead><tr><th>Strike{index_col}</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>
</section>"""


def page(data: dict[str, dict], conversions: dict, fixture: bool) -> str:
    sections = "".join(section(data[s], conversions.get(s)) for s in SYMBOLS)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>GEX heatmap</title><style>
body {{ font: 13px/1.4 system-ui, sans-serif; color: #18181b; background: #fff;
  margin: 24px; width: 1040px; }}
h1 {{ font-size: 20px; margin: 0 0 4px; }} h2 {{ font-size: 17px; margin: 24px 0 2px; }}
small, span, .note {{ color: #71717a; font-weight: normal; }} p {{ margin: 0 0 8px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #e4e4e7; padding: 3px 8px; text-align: right; white-space: nowrap; }}
tbody th, thead th:first-child {{ text-align: left; background: #fafafa; }}
tr.spot td {{ background: #eef2ff; color: #4338ca; text-align: center; font-weight: 600;
  border: 1px dashed #4338ca; }}
</style></head><body>
<h1>GEX heatmap: SPY and QQQ{" (SYNTHETIC FIXTURE DATA)" if fixture else ""}</h1>
<div class="note">Call-minus-put GEX proxy, USD per 1% move. Green = call-heavy, red = put-heavy.
Made {datetime.now(NY).strftime("%b %d, %Y %I:%M %p ET")}. Research only, not advice.</div>
{sections}
</body></html>
"""


def summary(d: dict, conversion: dict | None) -> list[str]:
    """Plain-text facts for the daily summary (docs/interpret-heatmap.md).

    Covers every strike and expiration in the snapshot (1-30 days), not only
    the window drawn on the page.
    """
    gex, spot = d["gex"], d["spot"]
    by_strike: dict[str, float] = {}
    by_expiration: dict[str, float] = {}
    for e, expiration in enumerate(gex["expirations"]):
        for s, strike in enumerate(gex["strikes"]):
            cell = gex["cells"][e][s]
            if cell and cell["signed_proxy"] is not None:
                by_strike[strike] = by_strike.get(strike, 0) + cell["signed_proxy"]
            if cell and cell["gross_exposure"] is not None:
                by_expiration[expiration] = by_expiration.get(expiration, 0) + cell["gross_exposure"]

    def where(strike: str) -> str:
        pct = (float(strike) / spot - 1) * 100
        index = f"~{conversion['index']} {float(strike) * conversion['ratio']:,.0f}, " if conversion else ""
        return f"{strike_label(strike)} ({index}{pct:+.1f}%)"

    ranked = sorted(by_strike.items(), key=lambda kv: kv[1])
    near = [v for k, v in by_strike.items() if abs(float(k) / spot - 1) <= 0.02]
    p0 = d["positioning"][0] if d["positioning"] else {}
    walls = ", ".join(f"{name} {where(p0[key])}" if p0.get(key) else f"{name} n/a" for name, key in LEVELS)
    dte = {p["expiration"]: p["dte"] for p in d["positioning"]}
    return [
        f"{d['symbol']}: spot ${spot:.2f}"
        + (f" (~{conversion['index']} {spot * conversion['ratio']:,.2f})" if conversion else ""),
        f"  data: {as_of(d)}",
        f"  net GEX, all strikes: {money(sum(by_strike.values()))}; within 2% of spot: {money(sum(near))}",
        "  biggest positive strikes: "
        + "; ".join(f"{where(k)}: {money(v)}" for k, v in ranked[::-1][:3] if v > 0),
        "  biggest negative strikes: " + "; ".join(f"{where(k)}: {money(v)}" for k, v in ranked[:3] if v < 0),
        f"  nearest expiration {p0.get('expiration', 'n/a')}: {walls}",
        "  largest expirations by gross GEX: "
        + "; ".join(
            f"{e} ({dte.get(e, '?')}d): {money(v)}"
            for e, v in sorted(by_expiration.items(), key=lambda kv: -kv[1])[:3]
        ),
    ]


def main() -> None:
    fixture = "--fixture" in sys.argv[1:]
    data = fetch(fixture)
    conversions = json.loads((ROOT / "reference/conversion_ratios.json").read_text())["ratios"]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page(data, conversions, fixture))
    if fixture:
        print("SYNTHETIC FIXTURE DATA -- not real prices")
    for symbol in SYMBOLS:
        print("\n".join(summary(data[symbol], conversions.get(symbol))))
    print(f"Heatmap page: {OUT}")


if __name__ == "__main__":
    main()
