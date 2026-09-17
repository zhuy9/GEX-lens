import { useMemo } from "react";
import Plot from "react-plotly.js";
import type { GexCell, GexData, GexMode } from "@/types";

interface GexHeatmapProps {
	gex: GexData;
	mode: GexMode;
}

const MILLIONS = 1_000_000;
const THOUSAND = 1_000;
const PLOT_HEIGHT_PX = 560; // fixed: keeps the x-axis pinned at a constant position
const ROW_HEIGHT_PX = 20; // minimum per-strike height for readable y-axis labels
const CHART_CHROME_PX = 130; // x-axis labels + margins
// Nearest 7 expirations, not the full 1-60 DTE fetch/eligibility scope (which
// stays fixed per the PRD) -- just how many columns this chart renders.
const MAX_VISIBLE_EXPIRATIONS = 7;

function fmtMillions(value: number | null): string {
	if (value === null) return "Unknown";
	return `$${(value / MILLIONS).toFixed(2)}M`;
}

function fmtCount(value: number | null): string {
	return value === null ? "Unknown" : value.toLocaleString();
}

/** Short on-cell label, e.g. "44.1M", "972K", "-781" (matches reference chart). */
function fmtCompact(value: number | null): string {
	if (value === null) return "";
	const abs = Math.abs(value);
	if (abs >= MILLIONS) return `${(value / MILLIONS).toFixed(1)}M`;
	if (abs >= THOUSAND) return `${(value / THOUSAND).toFixed(0)}K`;
	return `${value.toFixed(0)}`;
}

function cellRaw(cell: GexCell | null, mode: GexMode): number | null {
	if (cell === null) return null;
	return mode === "signed" ? cell.signed_proxy : cell.gross_exposure;
}

function cellValue(cell: GexCell | null, mode: GexMode): number | null {
	const raw = cellRaw(cell, mode);
	return raw === null ? null : raw / MILLIONS;
}

function cellHoverText(
	cell: GexCell | null,
	strike: string,
	expiration: string,
): string {
	if (cell === null) {
		return `Strike ${strike}<br>Expiration ${expiration}<br>No contracts in scope`;
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
	].join("<br>");
}

export function GexHeatmap({ gex, mode }: GexHeatmapProps) {
	const expirations = useMemo(
		() => gex.expirations.slice(0, MAX_VISIBLE_EXPIRATIONS),
		[gex.expirations],
	);

	const { z, text, hoverText, zmin, zmax } = useMemo(() => {
		// Transposed relative to gex.cells (which is [expiration][strike]) so
		// strike lands on the y-axis and expiration on the x-axis.
		const z = gex.strikes.map((_, strikeIndex) =>
			expirations.map((_, expIndex) =>
				cellValue(gex.cells[expIndex][strikeIndex], mode),
			),
		);
		const text = gex.strikes.map((_, strikeIndex) =>
			expirations.map((_, expIndex) =>
				fmtCompact(cellRaw(gex.cells[expIndex][strikeIndex], mode)),
			),
		);
		const hoverText = gex.strikes.map((strike, strikeIndex) =>
			expirations.map((expiration, expIndex) =>
				cellHoverText(gex.cells[expIndex][strikeIndex], strike, expiration),
			),
		);
		const values = z.flat().filter((v): v is number => v !== null);
		const maxAbs = values.length > 0 ? Math.max(...values.map(Math.abs)) : 0;
		if (mode === "signed") {
			const bound = maxAbs > 0 ? maxAbs : 1;
			return { z, text, hoverText, zmin: -bound, zmax: bound };
		}
		const bound = maxAbs > 0 ? maxAbs : 1;
		return { z, text, hoverText, zmin: 0, zmax: bound };
	}, [gex, mode, expirations]);

	if (gex.strikes.length === 0 || gex.expirations.length === 0) {
		return (
			<div className="flex h-96 items-center justify-center text-sm text-muted-foreground">
				No in-scope strikes or expirations in this snapshot.
			</div>
		);
	}

	// Show only as many strikes as fit at a readable row height; the rest are
	// reachable by dragging (dragmode "pan") without the x-axis ever moving,
	// since the plot's own height stays fixed.
	const visibleRows = Math.max(
		1,
		Math.floor((PLOT_HEIGHT_PX - CHART_CHROME_PX) / ROW_HEIGHT_PX),
	);
	const yaxisRange: [number, number] | undefined =
		gex.strikes.length > visibleRows ? [-0.5, visibleRows - 0.5] : undefined;

	return (
		<div style={{ height: PLOT_HEIGHT_PX }}>
			<Plot
				data={[
					{
						type: "heatmap",
						x: expirations,
						y: gex.strikes,
						z,
						text,
						texttemplate: "%{text}",
						textfont: { color: "#fff", size: 10 },
						hovertext: hoverText,
						hovertemplate: "%{hovertext}<extra></extra>",
						colorscale: mode === "signed" ? "RdBu" : "YlOrRd",
						reversescale: mode === "signed",
						zmid: mode === "signed" ? 0 : undefined,
						zmin,
						zmax,
						colorbar: { title: { text: "USD millions" } },
						xgap: 1,
						ygap: 1,
					},
				]}
				layout={{
					autosize: true,
					dragmode: "pan",
					margin: { l: 90, r: 20, t: 20, b: 60 },
					xaxis: { title: { text: "Expiration" }, type: "category" },
					yaxis: {
						title: { text: "Strike" },
						type: "category",
						range: yaxisRange,
					},
				}}
				style={{ width: "100%", height: "100%" }}
				useResizeHandler
				config={{
					displaylogo: false,
					responsive: true,
					modeBarButtonsToRemove: ["sendDataToCloud"],
				}}
			/>
		</div>
	);
}
