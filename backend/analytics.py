"""Pure quote filters, BSM, IV, gamma, GEX, surface."""

import math
from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple

from scipy.optimize import brentq
from scipy.stats import norm

from models import (
    GexCell,
    GexData,
    OptionQuote,
    PricedQuote,
    QualityCounts,
    SurfaceData,
    SurfaceObservation,
    calendar_dte,
    year_fraction,
)

IV_LOW, IV_HIGH = 1e-4, 5.0
IV_XTOL, IV_RTOL, IV_MAXITER = 1e-8, 1e-8, 100
REPRICE_TOLERANCE = 1e-5
MIN_MID = 0.05
MAX_RELATIVE_SPREAD = 0.50
MODEL_BOUNDS_TOLERANCE = 1e-8
MIN_TIME_VALUE = 0.01
# Strike scope is not defined here: app.py owns the one MIN/MAX_STRIKE_PCT
# constant (it also reports the value as policy in ConfigResponse/
# Parameters) and passes it into price_quote/analyze_snapshot below, so the
# reported scope and the executed scope can never drift apart (R12).
SURFACE_GRID: tuple[float, ...] = tuple(round(-0.20 + 0.01 * j, 2) for j in range(41))
MAX_BRIDGE_GAP = 0.05
MIN_SLICE_STRIKES = 3


def bsm_price(option_type: str, s: float, k: float, t: float, r: float, q: float, sigma: float) -> float:
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    if option_type == "C":
        return s * math.exp(-q * t) * norm.cdf(d1) - k * math.exp(-r * t) * norm.cdf(d2)
    return k * math.exp(-r * t) * norm.cdf(-d2) - s * math.exp(-q * t) * norm.cdf(-d1)


def bsm_gamma(s: float, k: float, t: float, r: float, q: float, sigma: float) -> float:
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    return math.exp(-q * t) * norm.pdf(d1) / (s * sigma * math.sqrt(t))


def _price_bounds(option_type: str, s: float, k: float, t: float, r: float, q: float) -> tuple[float, float]:
    disc_s = s * math.exp(-q * t)
    disc_k = k * math.exp(-r * t)
    if option_type == "C":
        return max(0.0, disc_s - disc_k), disc_s
    return max(0.0, disc_k - disc_s), disc_k


def solve_iv(
    option_type: str, s: float, k: float, t: float, r: float, q: float, mid: float
) -> tuple[float | None, str | None]:
    def f(sigma: float) -> float:
        return bsm_price(option_type, s, k, t, r, q, sigma) - mid

    f_lo, f_hi = f(IV_LOW), f(IV_HIGH)
    if abs(f_lo) <= 1e-8:
        sigma = IV_LOW
    elif abs(f_hi) <= 1e-8:
        sigma = IV_HIGH
    elif f_lo * f_hi > 0:
        return None, "IV_NOT_BRACKETED"
    else:
        try:
            sigma = brentq(f, IV_LOW, IV_HIGH, xtol=IV_XTOL, rtol=IV_RTOL, maxiter=IV_MAXITER)
        except (RuntimeError, ValueError):
            return None, "IV_SOLVER_FAILED"

    if abs(bsm_price(option_type, s, k, t, r, q, sigma) - mid) > REPRICE_TOLERANCE:
        return None, "IV_SOLVER_FAILED"
    return sigma, None


def price_quote(
    quote: OptionQuote,
    *,
    spot: float,
    r: float,
    q: float,
    valuation_at: datetime,
    min_calendar_dte: int,
    max_calendar_dte: int,
    min_strike_pct: float,
    max_strike_pct: float,
) -> PricedQuote:
    """Apply PRD 6.2's eligibility checks in order; retain the first exclusion reason."""
    dte = calendar_dte(quote.expiration, valuation_at)
    t = year_fraction(quote.expiration, valuation_at)
    strike = float(quote.strike)
    in_scope = (
        min_calendar_dte <= dte <= max_calendar_dte
        and min_strike_pct * spot <= strike <= max_strike_pct * spot
        and t > 0
    )
    if not in_scope:
        return PricedQuote(quote=quote, mid=None, iv=None, gamma=None, exclusion_reason="OUT_OF_SCOPE")

    # R12: _exposure_for below always multiplies by the fixed 100 in PRD 7's
    # formula -- it does not read quote.multiplier. A canonical record with
    # any other multiplier must not reach that formula silently; exclude by
    # the actual value, not only by whether a provider remembered to also
    # set the flag (the two could otherwise drift apart).
    if quote.multiplier != 100 or "NONSTANDARD_CONTRACT" in quote.flags:
        return PricedQuote(
            quote=quote, mid=None, iv=None, gamma=None, exclusion_reason="NONSTANDARD_CONTRACT"
        )

    bid, ask = quote.bid, quote.ask
    if (
        bid is None
        or ask is None
        or not math.isfinite(bid)
        or not math.isfinite(ask)
        or bid <= 0
        or ask < bid
    ):
        return PricedQuote(quote=quote, mid=None, iv=None, gamma=None, exclusion_reason="INVALID_BID_ASK")

    mid = (bid + ask) / 2
    if mid < MIN_MID:
        return PricedQuote(quote=quote, mid=mid, iv=None, gamma=None, exclusion_reason="LOW_MID")

    if (ask - bid) / mid > MAX_RELATIVE_SPREAD:
        return PricedQuote(quote=quote, mid=mid, iv=None, gamma=None, exclusion_reason="WIDE_SPREAD")

    lower, upper = _price_bounds(quote.option_type, spot, strike, t, r, q)
    if not (lower - MODEL_BOUNDS_TOLERANCE <= mid <= upper + MODEL_BOUNDS_TOLERANCE):
        return PricedQuote(quote=quote, mid=mid, iv=None, gamma=None, exclusion_reason="MODEL_PRICE_BOUNDS")

    if mid - lower <= MIN_TIME_VALUE:
        return PricedQuote(quote=quote, mid=mid, iv=None, gamma=None, exclusion_reason="LOW_TIME_VALUE")

    iv, error = solve_iv(quote.option_type, spot, strike, t, r, q, mid)
    if iv is None:
        return PricedQuote(quote=quote, mid=mid, iv=None, gamma=None, exclusion_reason=error)

    gamma = bsm_gamma(spot, strike, t, r, q, iv)
    return PricedQuote(quote=quote, mid=mid, iv=iv, gamma=gamma, exclusion_reason=None)


def _exposure_for(pq: PricedQuote | None, spot: float) -> float | None:
    if pq is None:
        return None
    oi, gamma = pq.quote.open_interest, pq.gamma
    if oi == 0:
        return 0.0
    if oi is not None and gamma is not None:
        return gamma * oi * 100 * spot * spot * 0.01
    return None


def build_gex(priced_quotes: tuple[PricedQuote, ...], spot: float) -> GexData:
    in_scope = [pq for pq in priced_quotes if pq.exclusion_reason != "OUT_OF_SCOPE"]
    strikes = sorted({pq.quote.strike for pq in in_scope})
    expirations = sorted({pq.quote.expiration for pq in in_scope})

    # Group the already-typed PricedQuote by side instead of extracting its
    # oi/gamma into a loose dict and rebuilding a typed GexCell from that.
    grid: dict[tuple[date, Decimal], dict[str, PricedQuote]] = {}
    for pq in in_scope:
        key = (pq.quote.expiration, pq.quote.strike)
        grid.setdefault(key, {})[pq.quote.option_type] = pq

    cells: list[list[GexCell | None]] = []
    for expiration in expirations:
        row: list[GexCell | None] = []
        for strike in strikes:
            sides = grid.get((expiration, strike))
            if sides is None:
                row.append(None)
                continue
            call_pq, put_pq = sides.get("C"), sides.get("P")
            call_exposure = _exposure_for(call_pq, spot)
            put_exposure = _exposure_for(put_pq, spot)
            if call_exposure is not None and put_exposure is not None:
                signed_proxy: float | None = call_exposure - put_exposure
                gross_exposure: float | None = call_exposure + put_exposure
                status = "COMPLETE"
            else:
                signed_proxy = None
                gross_exposure = None
                status = "INCOMPLETE"
            row.append(
                GexCell(
                    call_oi=call_pq.quote.open_interest if call_pq else None,
                    put_oi=put_pq.quote.open_interest if put_pq else None,
                    call_gamma=call_pq.gamma if call_pq else None,
                    put_gamma=put_pq.gamma if put_pq else None,
                    call_exposure=call_exposure,
                    put_exposure=put_exposure,
                    signed_proxy=signed_proxy,
                    gross_exposure=gross_exposure,
                    status=status,
                )
            )
        cells.append(row)

    return GexData(
        strikes=tuple(strikes), expirations=tuple(expirations), cells=tuple(tuple(r) for r in cells)
    )


def _piecewise_linear(xs: list[float], ws: list[float], x: float) -> float | None:
    if x < xs[0] or x > xs[-1]:
        return None
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            if x == xs[i]:
                return ws[i]
            if x == xs[i + 1]:
                return ws[i + 1]
            if xs[i + 1] - xs[i] > MAX_BRIDGE_GAP:
                return None
            frac = (x - xs[i]) / (xs[i + 1] - xs[i])
            return ws[i] + frac * (ws[i + 1] - ws[i])
    return None


def _has_adjacent_block(grid_iv: list[list[float | None]]) -> bool:
    for row_index in range(len(grid_iv) - 1):
        row_a, row_b = grid_iv[row_index], grid_iv[row_index + 1]
        for col in range(len(row_a) - 1):
            if (
                row_a[col] is not None
                and row_a[col + 1] is not None
                and row_b[col] is not None
                and row_b[col + 1] is not None
            ):
                return True
    return False


_EMPTY_SURFACE = SurfaceData(
    status="INSUFFICIENT_DATA", k=SURFACE_GRID, expirations=(), dte=(), iv=None, observations=()
)


class _Slice(NamedTuple):
    """One expiration's usable (k, w, strike) points plus its fractional T."""

    t: float
    points: list[tuple[float, float, Decimal]]


def build_surface(
    priced_quotes: tuple[PricedQuote, ...], spot: float, r: float, q: float, valuation_at: datetime
) -> SurfaceData:
    valid = [pq for pq in priced_quotes if pq.iv is not None]
    by_expiration: dict[date, list[PricedQuote]] = {}
    for pq in valid:
        by_expiration.setdefault(pq.quote.expiration, []).append(pq)

    slices: dict[date, _Slice] = {}
    for expiration, pqs in by_expiration.items():
        t = year_fraction(expiration, valuation_at)
        f = spot * math.exp((r - q) * t)
        by_strike: dict[Decimal, dict[str, PricedQuote]] = {}
        for pq in pqs:
            by_strike.setdefault(pq.quote.strike, {})[pq.quote.option_type] = pq

        points: list[tuple[float, float, Decimal]] = []
        for strike, sides in by_strike.items():
            k_float = float(strike)
            side = "P" if k_float < f else "C"
            chosen = sides.get(side)
            if chosen is None or chosen.iv is None:
                continue
            k = math.log(k_float / f)
            points.append((k, chosen.iv * chosen.iv * t, strike))

        if len({s for _, _, s in points}) < MIN_SLICE_STRIKES:
            continue
        points.sort(key=lambda item: item[0])
        slices[expiration] = _Slice(t=t, points=points)

    usable_expirations = sorted(slices.keys())
    if len(usable_expirations) < 2:
        return _EMPTY_SURFACE

    grid_iv: list[list[float | None]] = []
    for expiration in usable_expirations:
        points = slices[expiration].points
        t = slices[expiration].t
        xs = [p[0] for p in points]
        ws = [p[1] for p in points]
        row = []
        for k_j in SURFACE_GRID:
            w = _piecewise_linear(xs, ws, k_j)
            row.append(math.sqrt(w / t) if w is not None else None)
        grid_iv.append(row)

    if not _has_adjacent_block(grid_iv):
        return _EMPTY_SURFACE

    observations = tuple(
        SurfaceObservation(
            expiration=expiration,
            strike=strike,
            k=k,
            dte=slices[expiration].t * 365,
            iv=math.sqrt(w / slices[expiration].t),
        )
        for expiration in usable_expirations
        for k, w, strike in slices[expiration].points
    )

    return SurfaceData(
        status="READY",
        k=SURFACE_GRID,
        expirations=tuple(usable_expirations),
        dte=tuple(slices[e].t * 365 for e in usable_expirations),
        iv=tuple(tuple(row) for row in grid_iv),
        observations=observations,
    )


def build_quality_counts(
    priced_quotes: tuple[PricedQuote, ...], gex_cells_flat: list[GexCell | None], source_row_count: int
) -> QualityCounts:
    in_scope = [pq for pq in priced_quotes if pq.exclusion_reason != "OUT_OF_SCOPE"]
    exclusion_counts: dict[str, int] = {}
    for pq in priced_quotes:
        if pq.exclusion_reason:
            exclusion_counts[pq.exclusion_reason] = exclusion_counts.get(pq.exclusion_reason, 0) + 1
    return QualityCounts(
        source_rows=source_row_count,
        normalized_contracts=len(priced_quotes),
        in_scope_contracts=len(in_scope),
        valid_ivs=sum(1 for pq in priced_quotes if pq.iv is not None),
        known_oi_contracts=sum(1 for pq in in_scope if pq.quote.open_interest is not None),
        complete_gex_cells=sum(
            1 for cell in gex_cells_flat if cell is not None and cell.status == "COMPLETE"
        ),
        exclusion_counts=exclusion_counts,
    )


def analyze_snapshot(
    contracts: tuple[OptionQuote, ...],
    *,
    spot: float,
    r: float,
    q: float,
    valuation_at: datetime,
    min_calendar_dte: int,
    max_calendar_dte: int,
    min_strike_pct: float,
    max_strike_pct: float,
    source_row_count: int,
) -> tuple[tuple[PricedQuote, ...], GexData, SurfaceData, QualityCounts]:
    priced = tuple(
        price_quote(
            c,
            spot=spot,
            r=r,
            q=q,
            valuation_at=valuation_at,
            min_calendar_dte=min_calendar_dte,
            max_calendar_dte=max_calendar_dte,
            min_strike_pct=min_strike_pct,
            max_strike_pct=max_strike_pct,
        )
        for c in contracts
    )
    gex = build_gex(priced, spot)
    surface = build_surface(priced, spot, r, q, valuation_at)
    flat_cells = [cell for row in gex.cells for cell in row]
    quality = build_quality_counts(priced, flat_cells, source_row_count)
    return priced, gex, surface, quality
