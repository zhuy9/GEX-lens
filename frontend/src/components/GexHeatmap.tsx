import { useMemo } from "react";
import Plot from "react-plotly.js";
import type { GexCell, GexData, GexMode } from "@/types";

interface GexHeatmapProps {
	gex: GexData;
	mode: GexMode;
}

const MILLIONS = 1_000_000;

function fmtMillions(value: number | null): string {
	if (value === null) return "Unknown";
	return `$${(value / MILLIONS).toFixed(2)}M`;
}

function fmtCount(value: number | null): string {
	return value === null ? "Unknown" : value.toLocaleString();
}

function cellValue(cell: GexCell | null, mode: GexMode): number | null {
	if (cell === null) return null;
	const raw = mode === "signed" ? cell.signed_proxy : cell.gross_exposure;
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
	const { z, text, zmin, zmax } = useMemo(() => {
		const z = gex.cells.map((row) => row.map((cell) => cellValue(cell, mode)));
		const text = gex.cells.map((row, rowIndex) =>
			row.map((cell, colIndex) =>
				cellHoverText(cell, gex.strikes[colIndex], gex.expirations[rowIndex]),
			),
		);
		const values = z.flat().filter((v): v is number => v !== null);
		const maxAbs = values.length > 0 ? Math.max(...values.map(Math.abs)) : 0;
		if (mode === "signed") {
			const bound = maxAbs > 0 ? maxAbs : 1;
			return { z, text, zmin: -bound, zmax: bound };
		}
		const bound = maxAbs > 0 ? maxAbs : 1;
		return { z, text, zmin: 0, zmax: bound };
	}, [gex, mode]);

	if (gex.strikes.length === 0 || gex.expirations.length === 0) {
		return (
			<div className="flex h-96 items-center justify-center text-sm text-muted-foreground">
				No in-scope strikes or expirations in this snapshot.
			</div>
		);
	}

	return (
		<Plot
			data={[
				{
					type: "heatmap",
					x: gex.strikes,
					y: gex.expirations,
					z,
					text,
					hovertemplate: "%{text}<extra></extra>",
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
				margin: { l: 90, r: 20, t: 20, b: 60 },
				xaxis: { title: { text: "Strike" }, type: "category" },
				yaxis: { title: { text: "Expiration" }, type: "category" },
			}}
			style={{ width: "100%", height: "100%" }}
			useResizeHandler
			config={{ displaylogo: false, responsive: true }}
		/>
	);
}
