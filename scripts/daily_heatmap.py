"""Fetch SPY + QQQ and write the daily facts (text) and heatmap images.

    cd backend && pip install -r ../scripts/requirements.txt
    python ../scripts/daily_heatmap.py            # live Nasdaq data
    python ../scripts/daily_heatmap.py --fixture  # synthetic data, no network

Writes (git-ignored) exports/heatmap.md -- facts plus the heatmap as a text
table, for AI agents -- and exports/heatmap-spy.png / heatmap-qqq.png for
people, drawn with matplotlib so no browser is needed (ADR-0004). Prints the facts the daily
summary uses, including the gamma flip and vanna exposure (ADR-0005). Uses the
backend's own refresh code with its own temporary settings and database, so
it never touches backend/settings.json or your saved snapshots.
"""

import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")  # render to files only
matplotlib.rcParams["text.parse_math"] = False  # "$780" is a price, not math
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app  # noqa: E402
import storage  # noqa: E402
from models import Settings  # noqa: E402

SYMBOLS = ("SPY", "QQQ")
EXPORTS = ROOT / "exports"
STRIKES_SHOWN = 17  # same spot-centered window as the app
EXPIRATIONS_SHOWN = 8
NY = ZoneInfo("America/New_York")
# Same colors as frontend/src/components/GexHeatmap.tsx: red-600, zinc-50, green-600.
CMAP = LinearSegmentedColormap.from_list("gex", ["#dc2626", "#fafafa", "#16a34a"])
CMAP.set_bad("#ffffff")
LEVELS = (("Call wall", "call_wall_strike"), ("Put wall", "put_wall_strike"), ("Max pain", "max_pain_strike"))


def fetch(fixture: bool) -> dict[str, dict]:
    """One fresh dashboard per symbol (gamma_profile included, ADR-0005)."""
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
    sign, v = ("-" if value < 0 else "+" if value > 0 else ""), abs(value)
    if v >= 1e9:
        return f"{sign}${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.1f}M"
    return f"{sign}${v / 1e3:.0f}K" if v >= 1e3 else f"{sign}${v:.0f}"


def strike_label(strike: str | float) -> str:
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


def facts(d: dict, conversion: dict | None) -> dict:
    """Every number the summary and the image use. Covers all strikes and
    expirations in the snapshot (1-30 days), not only the drawn window."""
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

    def where(strike: str | float) -> str:
        pct = (float(strike) / spot - 1) * 100
        index = f"~{conversion['index']} {float(strike) * conversion['ratio']:,.0f}, " if conversion else ""
        return f"{strike_label(strike)} ({index}{pct:+.1f}%)"

    ranked = sorted(by_strike.items(), key=lambda kv: kv[1])
    p0 = d["positioning"][0] if d["positioning"] else {}
    dte = {p["expiration"]: p["dte"] for p in d["positioning"]}
    gp = d["gamma_profile"]
    return {
        "spot": spot,
        "index": f"~{conversion['index']} {spot * conversion['ratio']:,.2f}" if conversion else "",
        "net_gex": gp["net_gex_at_spot"],
        "near_gex": sum(v for k, v in by_strike.items() if abs(float(k) / spot - 1) <= 0.02),
        "flip": gp["gamma_flip"],
        "where": where,
        "vex": gp["vanna_exposure"],
        "top_vex": gp["top_vanna_strikes"],
        "top_pos": [(k, v) for k, v in ranked[::-1][:3] if v > 0],
        "top_neg": [(k, v) for k, v in ranked[:3] if v < 0],
        "walls": ", ".join(f"{n} {where(p0[k])}" if p0.get(k) else f"{n} n/a" for n, k in LEVELS),
        "nearest": p0.get("expiration", "n/a"),
        "big_dates": [
            (e, dte.get(e, "?"), v) for e, v in sorted(by_expiration.items(), key=lambda kv: -kv[1])[:3]
        ],
    }


def regime_line(f: dict) -> str:
    env = "POSITIVE" if f["net_gex"] > 0 else "NEGATIVE"
    flip = f"gamma flip {f['where'](f['flip'])}" if f["flip"] else "no gamma flip within ±10% of spot"
    return f"{env} gamma (net GEX at spot {money(f['net_gex'])} per 1% move); {flip}"


def vex_line(f: dict) -> str:
    side = "buys" if f["vex"] > 0 else "sells"
    return (
        f"VEX (vanna) {money(f['vex'])} per vol point: if IV falls 1 point, hedging {side} about "
        f"{money(abs(f['vex']))[1:]} of stock (if IV rises, the opposite)"
    )


def summary(symbol: str, d: dict, f: dict) -> list[str]:
    def pairs(items: list) -> str:
        return "; ".join(f"{f['where'](k)}: {money(v)}" for k, v in items) or "none"

    return [
        f"{symbol}: spot ${f['spot']:.2f}" + (f" ({f['index']})" if f["index"] else ""),
        f"  data: {as_of(d)}",
        f"  {regime_line(f)}",
        f"  {vex_line(f)}",
        f"  net GEX within 2% of spot: {money(f['near_gex'])}",
        f"  biggest positive strikes: {pairs(f['top_pos'])}",
        f"  biggest negative strikes: {pairs(f['top_neg'])}",
        f"  biggest VEX strikes: {pairs(f['top_vex'])}",
        f"  nearest expiration {f['nearest']}: {f['walls']}",
        "  largest expirations by gross GEX: "
        + "; ".join(f"{e} ({n}d): {money(v)[1:]}" for e, n, v in f["big_dates"]),
    ]


def row_position(strikes: list[float], level: float) -> float | None:
    """Fractional row index of a price level in a highest-first strike list."""
    for i in range(len(strikes) - 1):
        hi, lo = strikes[i], strikes[i + 1]
        if hi >= level >= lo:
            return i + (hi - level) / (hi - lo)
    return None


def window(d: dict) -> tuple[list[str], list[str], np.ndarray]:
    """The drawn window: strikes around spot (highest first), nearest
    expirations, and their signed GEX (NaN = no data). Shared by the image
    and the text table so both show the same cells."""
    gex, spot = d["gex"], d["spot"]
    strikes, expirations = gex["strikes"], gex["expirations"][:EXPIRATIONS_SHOWN]
    nearest = min(range(len(strikes)), key=lambda i: abs(float(strikes[i]) - spot))
    start = max(0, min(nearest - STRIKES_SHOWN // 2, len(strikes) - STRIKES_SHOWN))
    rows = list(range(start, min(len(strikes), start + STRIKES_SHOWN)))[::-1]  # highest first
    grid = np.array(
        [
            [
                c["signed_proxy"] if (c := gex["cells"][e][s]) and c["signed_proxy"] is not None else np.nan
                for e in range(len(expirations))
            ]
            for s in rows
        ]
    )
    return [strikes[s] for s in rows], expirations, grid


def text_grid(d: dict, f: dict, conversion: dict | None) -> list[str]:
    """The same window as a Markdown table, with spot and flip marker rows."""
    strikes, expirations, grid = window(d)
    dte = {p["expiration"]: p["dte"] for p in d["positioning"]}
    ratio = conversion["ratio"] if conversion else None
    head = "Strike" + (f" (~{conversion['index']})" if conversion else "")
    lines = [
        f"| {head} | " + " | ".join(f"{e} ({dte.get(e, '?')}d)" for e in expirations) + " |",
        "|---|" + "---:|" * len(expirations),
    ]
    marks = [(d["spot"], f"spot ${d['spot']:.2f}")]
    if f["flip"]:
        marks.append((f["flip"], f"gamma flip {strike_label(f['flip'])}"))
    marks.sort(reverse=True)  # highest first, like the rows
    blank = " |" * len(expirations)
    for strike, values in zip(strikes, grid, strict=True):
        while marks and marks[0][0] > float(strike):
            lines.append(f"| **{marks.pop(0)[1]}** |{blank}")
        label = strike_label(strike) + (f" (~{float(strike) * ratio:,.0f})" if ratio else "")
        lines.append(f"| {label} | " + " | ".join(money(v) if np.isfinite(v) else "" for v in values) + " |")
    lines += [f"| **{text}** |{blank}" for _, text in marks]  # below the window
    return lines


def render(symbol: str, d: dict, f: dict, conversion: dict | None, fixture: bool, path: Path) -> None:
    spot = d["spot"]
    strike_rows, expirations, grid = window(d)
    bound = float(np.nanmax(np.abs(grid))) if np.isfinite(grid).any() else 1.0
    dte = {p["expiration"]: p["dte"] for p in d["positioning"]}

    fig, ax = plt.subplots(figsize=(11, 8.2), dpi=150)
    ax.imshow(np.ma.masked_invalid(grid), cmap=CMAP, norm=TwoSlopeNorm(0, -bound, bound), aspect="auto")
    for (r, c), v in np.ndenumerate(grid):
        if np.isfinite(v):
            color = "white" if abs(v) / bound > 0.45 else "#18181b"
            ax.text(c, r, money(v).lstrip("+"), ha="center", va="center", fontsize=8, color=color, zorder=3)
    ax.set_xticks(
        range(len(expirations)),
        [f"{datetime.fromisoformat(e):%b %d}\n{dte.get(e, '?')}d" for e in expirations],
    )
    ax.xaxis.tick_top()
    ratio = conversion["ratio"] if conversion else None
    ax.set_yticks(
        range(len(strike_rows)),
        [strike_label(s) + (f"  ·  {float(s) * ratio:,.0f}" if ratio else "") for s in strike_rows],
    )
    ax.tick_params(length=0, labelsize=9)
    ax.set_xticks(np.arange(-0.5, len(expirations)), minor=True)
    ax.set_yticks(np.arange(-0.5, len(strike_rows)), minor=True)
    ax.grid(which="minor", color="#e4e4e7", linewidth=0.8)
    ax.tick_params(which="minor", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    shown = [float(s) for s in strike_rows]
    marks = [(spot, "#4338ca", f"spot ${spot:.2f}")]
    if f["flip"]:
        marks.append((f["flip"], "#d97706", f"gamma flip {strike_label(f['flip'])}"))
    for level, color, text in marks:
        y = row_position(shown, level)
        if y is not None:
            ax.axhline(y, color=color, linestyle="--", linewidth=1.4, zorder=1)  # behind cell labels
            ax.text(
                len(expirations) - 0.45,
                y,
                f" {text}",
                color=color,
                va="center",
                fontsize=9,
                fontweight="bold",
            )

    title = f"{symbol} GEX heatmap" + ("  (SYNTHETIC FIXTURE DATA)" if fixture else "")
    fig.suptitle(title, x=0.02, ha="left", fontsize=15, fontweight="bold")
    index = f" ({f['index']})" if f["index"] else ""
    header = f"Spot ${spot:.2f}{index}  ·  {as_of(d)}\n{regime_line(f)}\n{vex_line(f)}"
    fig.text(0.02, 0.925, header, fontsize=9, color="#3f3f46", va="top")
    rows_note = f" (and {conversion['index']} level)" if conversion else ""
    fig.text(
        0.02,
        0.015,
        f"Cells: call-minus-put GEX, USD per 1% move. Green = call gamma, red = put gamma. "
        f"Rows: strike{rows_note}. Research only. Not financial advice.",
        fontsize=8,
        color="#71717a",
    )
    fig.subplots_adjust(left=0.13, right=0.82, top=0.79, bottom=0.05)
    fig.savefig(path, facecolor="white")
    plt.close(fig)


def main() -> None:
    fixture = "--fixture" in sys.argv[1:]
    data = fetch(fixture)
    conversions = json.loads((ROOT / "reference/conversion_ratios.json").read_text())["ratios"]
    EXPORTS.mkdir(exist_ok=True)
    text = ["SYNTHETIC FIXTURE DATA -- not real prices", ""] if fixture else []
    text += [
        "# GEX daily data",
        "",
        "Research only. Not financial advice. Include this line in any summary you send.",
        "",
        "Cells: call-minus-put GEX, USD per 1% move (+ call gamma, - put gamma).",
        "",
    ]
    for symbol, d in data.items():
        f = facts(d, conversions.get(symbol))
        image = EXPORTS / f"heatmap-{symbol.lower()}.png"
        render(symbol, d, f, conversions.get(symbol), fixture, image)
        lines = summary(symbol, d, f)
        print("\n".join(lines))
        print(f"  image (for people): {image}")
        text += [
            f"## {symbol}",
            "",
            "```text",
            *lines,
            "```",
            "",
            *text_grid(d, f, conversions.get(symbol)),
            "",
        ]
    (EXPORTS / "heatmap.md").write_text("\n".join(text))
    print(f"Text for AI agents (facts + heatmap table): {EXPORTS / 'heatmap.md'}")


if __name__ == "__main__":
    main()
