import type { Config, Data, Layout } from "plotly.js";
import { memo, useEffect, useMemo, useState } from "react";
import Plot from "react-plotly.js";
import type { SurfaceData } from "@/types";

interface IvSurfaceProps {
	surface: SurfaceData;
}

// Neither depends on props, so hoisted for a stable reference across every
// render -- Plotly (and React.memo on the parent) treat a new object
// identity as "this changed," which resets state like camera position.
const LAYOUT: Partial<Layout> = {
	autosize: true,
	margin: { l: 0, r: 0, t: 20, b: 0 },
	scene: {
		xaxis: { title: { text: "log-forward-moneyness (k)" } },
		yaxis: { title: { text: "DTE (fractional days)" } },
		zaxis: { title: { text: "IV (%)" } },
	},
};
const CONFIG: Partial<Config> = {
	displaylogo: false,
	responsive: true,
	modeBarButtonsToRemove: ["sendChartToCloud"],
};

export const IvSurface = memo(function IvSurface({ surface }: IvSurfaceProps) {
	// Plotly can fail to initialize (e.g. no WebGL) without throwing a React
	// render exception -- react-plotly.js surfaces that via onError instead,
	// which an error boundary alone can never catch.
	const [plotError, setPlotError] = useState(false);
	// biome-ignore lint/correctness/useExhaustiveDependencies: surface is a prop, not a module-scope value; a new snapshot must clear a stale plot error.
	useEffect(() => setPlotError(false), [surface]);

	const observedTrace = useMemo(() => {
		const x = surface.observations.map((o) => o.k);
		const y = surface.observations.map((o) => o.dte);
		const z = surface.observations.map((o) => o.iv * 100);
		const text = surface.observations.map(
			(o) =>
				`Observed<br>Expiration ${o.expiration}<br>Strike ${o.strike}<br>Moneyness (k) ${o.k.toFixed(4)}<br>IV ${(o.iv * 100).toFixed(2)}%`,
		);
		return { x, y, z, text };
	}, [surface.observations]);

	const zPercent = useMemo(() => {
		if (surface.iv === null) return null;
		return surface.iv.map((row) =>
			row.map((iv) => (iv === null ? null : iv * 100)),
		);
	}, [surface.iv]);

	const data = useMemo((): Data[] => {
		if (zPercent === null) return [];
		// One hover string per grid cell, shaped like zPercent, so a point on
		// the interpolated surface can be inspected too -- previously only
		// the observed-point markers had hover text at all.
		const surfaceHoverText = zPercent.map((row, rowIndex) =>
			row.map((ivPct, colIndex) => {
				const expiration = surface.expirations[rowIndex] ?? "Unknown";
				const dte = surface.dte[rowIndex];
				const k = surface.k[colIndex];
				const ivText = ivPct === null ? "Unknown" : `${ivPct.toFixed(2)}%`;
				return `Surface (interpolated)<br>Expiration ${expiration}<br>Moneyness (k) ${k.toFixed(4)}<br>DTE ${dte.toFixed(2)}<br>IV ${ivText}`;
			}),
		);
		return [
			{
				type: "surface",
				x: surface.k,
				y: surface.dte,
				z: zPercent,
				connectgaps: false,
				showscale: false,
				// @types/plotly.js types `text` as string | string[] even for a
				// surface trace, but Plotly.js itself accepts a 2D matrix shaped
				// like z at runtime -- this is a type-definition gap, not a
				// runtime workaround.
				text: surfaceHoverText as unknown as string[],
				hovertemplate: "%{text}<extra></extra>",
				opacity: 0.85,
			},
			{
				type: "scatter3d",
				mode: "markers",
				x: observedTrace.x,
				y: observedTrace.y,
				z: observedTrace.z,
				text: observedTrace.text,
				hovertemplate: "%{text}<extra></extra>",
				marker: { size: 3, color: "black" },
			},
		];
	}, [surface.k, surface.dte, surface.expirations, zPercent, observedTrace]);

	if (surface.status === "INSUFFICIENT_DATA" || surface.iv === null) {
		return (
			<div className="flex h-96 items-center justify-center text-center text-sm text-muted-foreground">
				Not enough valid observations for a surface in this snapshot.
				<br />
				No surface is shown rather than an invented one.
			</div>
		);
	}

	if (plotError) {
		return (
			<div className="flex h-96 items-center justify-center text-center text-sm text-muted-foreground">
				This panel failed to render. Your browser or GPU may not support WebGL.
			</div>
		);
	}

	return (
		<Plot
			data={data}
			layout={LAYOUT}
			style={{ width: "100%", height: "100%" }}
			useResizeHandler
			config={CONFIG}
			onError={() => setPlotError(true)}
		/>
	);
});
