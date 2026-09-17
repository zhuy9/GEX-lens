import { useMemo } from "react";
import Plot from "react-plotly.js";
import type { SurfaceData } from "@/types";

interface IvSurfaceProps {
	surface: SurfaceData;
}

export function IvSurface({ surface }: IvSurfaceProps) {
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

	if (surface.status === "INSUFFICIENT_DATA" || surface.iv === null) {
		return (
			<div className="flex h-96 items-center justify-center text-center text-sm text-muted-foreground">
				Not enough valid observations for a surface in this snapshot.
				<br />
				No surface is shown rather than an invented one.
			</div>
		);
	}

	const zPercent = surface.iv.map((row) =>
		row.map((iv) => (iv === null ? null : iv * 100)),
	);

	return (
		<Plot
			data={[
				{
					type: "surface",
					x: surface.k,
					y: surface.dte,
					z: zPercent,
					connectgaps: false,
					showscale: false,
					hoverinfo: "skip",
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
			]}
			layout={{
				autosize: true,
				margin: { l: 0, r: 0, t: 20, b: 0 },
				scene: {
					xaxis: { title: { text: "log-forward-moneyness (k)" } },
					yaxis: { title: { text: "DTE (fractional days)" } },
					zaxis: { title: { text: "IV (%)" } },
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
	);
}
