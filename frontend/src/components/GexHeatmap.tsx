import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import type { GexCell, GexData, GexMode } from "@/types";

interface GexHeatmapProps {
	gex: GexData;
	mode: GexMode;
	spot: number;
	valuationAt: string;
}

const MILLIONS = 1_000_000;
const THOUSAND = 1_000;
const WINDOW_SIZE = 17; // strikes shown by default, centered on spot
const NEG_COLOR: [number, number, number] = [220, 38, 38]; // red-600, put-heavy
const POS_COLOR: [number, number, number] = [22, 163, 74]; // green-600, call-heavy
const GROSS_COLOR: [number, number, number] = [217, 119, 6]; // amber-600
const NEUTRAL: [number, number, number] = [250, 250, 250]; // zinc-50, zero baseline

function fmtMillions(value: number | null): string {
	if (value === null) return "Unknown";
	return `$${(value / MILLIONS).toFixed(2)}M`;
}

function fmtCount(value: number | null): string {
	return value === null ? "Unknown" : value.toLocaleString();
}

/** Short signed dollar label, e.g. "$17.8M", "-$2.0M", "$0" (matches reference chart). */
function fmtDollarsCompact(value: number | null): string {
	if (value === null) return "";
	const sign = value < 0 ? "-" : "";
	const abs = Math.abs(value);
	if (abs >= MILLIONS) return `${sign}$${(abs / MILLIONS).toFixed(1)}M`;
	if (abs >= THOUSAND) return `${sign}$${(abs / THOUSAND).toFixed(0)}K`;
	return `${sign}$${abs.toFixed(0)}`;
}

function fmtStrike(strike: string): string {
	const n = Number(strike);
	return `$${n % 1 === 0 ? n.toFixed(0) : n.toFixed(2)}`;
}

/** Display-only approximation (UTC calendar days); the server owns the
 * authoritative NY-timezone calendar_dte used for pricing/eligibility. */
function displayDte(expirationIso: string, valuationAtIso: string): number {
	const expUtcMidnight = Date.parse(`${expirationIso}T00:00:00Z`);
	const val = new Date(valuationAtIso);
	const valUtcMidnight = Date.UTC(
		val.getUTCFullYear(),
		val.getUTCMonth(),
		val.getUTCDate(),
	);
	return Math.round((expUtcMidnight - valUtcMidnight) / 86_400_000);
}

function fmtExpirationHeader(expirationIso: string): string {
	return new Date(`${expirationIso}T00:00:00Z`).toLocaleDateString("en-US", {
		timeZone: "UTC",
		month: "short",
		day: "numeric",
	});
}

function cellRaw(cell: GexCell | null, mode: GexMode): number | null {
	if (cell === null) return null;
	return mode === "signed" ? cell.signed_proxy : cell.gross_exposure;
}

function cellTitle(
	cell: GexCell | null,
	strike: string,
	expiration: string,
): string {
	if (cell === null) {
		return `Strike ${strike}\nExpiration ${expiration}\nNo contracts in scope`;
	}
	return [
		`Strike ${strike}`,
		`Expiration ${expiration}`,
		`Call OI: ${fmtCount(cell.call_oi)}`,
		`Put OI: ${fmtCount(cell.put_oi)}`,
		`Call gamma: ${cell.call_gamma ?? "Unknown"}`,
		`Put gamma: ${cell.put_gamma ?? "Unknown"}`,
		`Call exposure: ${fmtMillions(cell.call_exposure)}`,
		`Put exposure: ${fmtMillions(cell.put_exposure)}`,
		`Signed proxy: ${fmtMillions(cell.signed_proxy)}`,
		`Gross exposure: ${fmtMillions(cell.gross_exposure)}`,
		`Status: ${cell.status}`,
	].join("\n");
}

function mixChannel(a: number, b: number, t: number): number {
	return Math.round(a + (b - a) * t);
}

function magnitudeFraction(value: number | null, bound: number): number {
	if (value === null || bound <= 0) return 0;
	return Math.min(1, Math.abs(value) / bound);
}

function cellBackground(
	value: number | null,
	bound: number,
	mode: GexMode,
): string | undefined {
	if (value === null) return undefined; // gap: no fill, not a zero-colored cell
	const target =
		mode === "signed" ? (value >= 0 ? POS_COLOR : NEG_COLOR) : GROSS_COLOR;
	const t = magnitudeFraction(value, bound);
	const [r, g, b] = [0, 1, 2].map((i) => mixChannel(NEUTRAL[i], target[i], t));
	return `rgb(${r}, ${g}, ${b})`;
}

function cellTextColor(
	value: number | null,
	bound: number,
): string | undefined {
	if (value === null) return undefined;
	return magnitudeFraction(value, bound) > 0.45 ? "#fff" : "#18181b";
}

export function GexHeatmap({ gex, mode, spot, valuationAt }: GexHeatmapProps) {
	const [expanded, setExpanded] = useState(false);

	const bound = useMemo(() => {
		const values = gex.cells
			.flat()
			.map((cell) => cellRaw(cell, mode))
			.filter((v): v is number => v !== null)
			.map(Math.abs);
		return values.length > 0 ? Math.max(...values) : 1;
	}, [gex, mode]);

	if (gex.strikes.length === 0 || gex.expirations.length === 0) {
		return (
			<div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
				No in-scope strikes or expirations in this snapshot.
			</div>
		);
	}

	const nearestIndex = gex.strikes.reduce((best, strike, i) => {
		const diff = Math.abs(Number(strike) - spot);
		const bestDiff = Math.abs(Number(gex.strikes[best]) - spot);
		return diff < bestDiff ? i : best;
	}, 0);

	let visibleIndices = gex.strikes.map((_, i) => i);
	const isWindowed = !expanded && gex.strikes.length > WINDOW_SIZE;
	if (isWindowed) {
		const half = Math.floor(WINDOW_SIZE / 2);
		let start = Math.max(0, nearestIndex - half);
		const end = Math.min(gex.strikes.length, start + WINDOW_SIZE);
		start = Math.max(0, end - WINDOW_SIZE);
		visibleIndices = Array.from({ length: end - start }, (_, i) => start + i);
	}
	// Highest strike first, like an order book.
	const rowIndices = [...visibleIndices].reverse();

	// Insert a divider row at spot's exact position (it usually falls between
	// two strikes, not on one).
	const rows: (number | "spot")[] = [];
	let spotPlaced = false;
	for (const strikeIndex of rowIndices) {
		if (!spotPlaced && Number(gex.strikes[strikeIndex]) < spot) {
			rows.push("spot");
			spotPlaced = true;
		}
		rows.push(strikeIndex);
	}
	if (!spotPlaced) rows.push("spot");

	return (
		<div className="flex flex-col gap-2">
			<div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
				{mode === "signed" ? (
					<div className="flex items-center gap-2">
						<span>Put-heavy</span>
						<div
							className="h-2 w-28 rounded-full"
							style={{
								background: `linear-gradient(to right, rgb(${NEG_COLOR.join(",")}), rgb(${NEUTRAL.join(",")}), rgb(${POS_COLOR.join(",")}))`,
							}}
						/>
						<span>Call-heavy</span>
					</div>
				) : (
					<div className="flex items-center gap-2">
						<span>Low</span>
						<div
							className="h-2 w-28 rounded-full"
							style={{
								background: `linear-gradient(to right, rgb(${NEUTRAL.join(",")}), rgb(${GROSS_COLOR.join(",")}))`,
							}}
						/>
						<span>High</span>
					</div>
				)}
				<Button
					variant="outline"
					size="sm"
					onClick={() => setExpanded((v) => !v)}
				>
					{expanded ? "Collapse" : `Expand (${gex.strikes.length} strikes)`}
				</Button>
			</div>

			<div className="max-h-[520px] overflow-auto rounded-md border">
				<table className="w-full border-collapse text-xs">
					<thead>
						<tr>
							<th className="sticky top-0 left-0 z-20 border-r border-b bg-background px-2 py-1.5 text-left">
								Strike
							</th>
							{gex.expirations.map((expiration) => (
								<th
									key={expiration}
									className="sticky top-0 z-10 border-b bg-background px-2 py-1.5 text-right font-medium whitespace-nowrap"
								>
									<div>{fmtExpirationHeader(expiration)}</div>
									<div className="font-normal text-muted-foreground">
										{displayDte(expiration, valuationAt)}d
									</div>
								</th>
							))}
						</tr>
					</thead>
					<tbody>
						{rows.map((row) => {
							if (row === "spot") {
								return (
									<tr key="spot" className="bg-primary/10">
										<td
											colSpan={gex.expirations.length + 1}
											className="sticky left-0 border-t border-b border-dashed border-primary px-2 py-1 text-center font-medium text-primary"
										>
											Underlying ${spot.toFixed(2)}
										</td>
									</tr>
								);
							}
							const strikeIndex = row;
							const strike = gex.strikes[strikeIndex];
							return (
								<tr key={strike}>
									<td className="sticky left-0 z-10 border-r bg-background px-2 py-1 font-medium whitespace-nowrap">
										{fmtStrike(strike)}
									</td>
									{gex.expirations.map((expiration, expIndex) => {
										const cell = gex.cells[expIndex][strikeIndex];
										const value = cellRaw(cell, mode);
										return (
											<td
												key={expiration}
												title={cellTitle(cell, strike, expiration)}
												className="px-2 py-1 text-right tabular-nums whitespace-nowrap"
												style={{
													backgroundColor: cellBackground(value, bound, mode),
													color: cellTextColor(value, bound),
												}}
											>
												{value === null ? "" : fmtDollarsCompact(value)}
											</td>
										);
									})}
								</tr>
							);
						})}
					</tbody>
				</table>
			</div>

			<div className="text-center text-xs text-muted-foreground">
				Showing {rowIndices.length} of {gex.strikes.length} strikes
				{isWindowed && " around spot"}
				{gex.strikes.length > WINDOW_SIZE && (
					<>
						{" · "}
						<button
							type="button"
							className="underline underline-offset-2"
							onClick={() => setExpanded((v) => !v)}
						>
							{expanded ? "show fewer" : "view all"}
						</button>
					</>
				)}
			</div>
		</div>
	);
}
