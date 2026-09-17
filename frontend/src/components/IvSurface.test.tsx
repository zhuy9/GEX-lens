import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SurfaceData } from "@/types";
import { IvSurface } from "./IvSurface";

// Captures the real props IvSurface passes to <Plot>, instead of only a
// trace count -- R14: the main suite only ever exercised
// surface.status="INSUFFICIENT_DATA" (test-fixtures.ts's default), so a
// READY surface's grid, observed markers, hover text, and units were never
// actually verified to render correctly.
const { capturedProps } = vi.hoisted(() => ({
	capturedProps: { current: null as unknown },
}));
vi.mock("react-plotly.js", () => ({
	default: (props: unknown) => {
		capturedProps.current = props;
		return <div data-testid="plotly-mock" />;
	},
}));

// biome-ignore lint/suspicious/noExplicitAny: react-plotly.js's real prop types aren't needed for this assertion surface
type PlotProps = any;

afterEach(() => {
	cleanup();
	capturedProps.current = null;
});

const READY_SURFACE: SurfaceData = {
	status: "READY",
	k: [-0.1, 0, 0.1],
	expirations: ["2026-01-15", "2026-02-15"],
	dte: [14, 45],
	iv: [
		[0.22, 0.2, 0.21],
		[0.24, 0.23, 0.25],
	],
	observations: [
		{ expiration: "2026-01-15", strike: "100", k: 0, dte: 14, iv: 0.2 },
	],
};

describe("R14: IvSurface renders a READY surface with grid + observed traces and hover", () => {
	it("passes both a surface trace and an observed-marker trace to Plot", () => {
		render(<IvSurface surface={READY_SURFACE} />);

		expect(screen.getByTestId("plotly-mock")).toBeInTheDocument();
		const props = capturedProps.current as PlotProps;
		expect(props.data).toHaveLength(2);
		expect(props.data[0].type).toBe("surface");
		expect(props.data[1].type).toBe("scatter3d");
	});

	it("gives the surface grid its own hover text, distinct from observed points", () => {
		render(<IvSurface surface={READY_SURFACE} />);

		const props = capturedProps.current as PlotProps;
		const [surfaceTrace, observedTrace] = props.data;
		expect(surfaceTrace.hovertemplate).toBe("%{text}<extra></extra>");
		expect(surfaceTrace.text[0][0]).toMatch(/^Surface \(interpolated\)/);
		expect(surfaceTrace.text[0][0]).toMatch(/Moneyness \(k\)/);
		expect(observedTrace.text[0]).toMatch(/^Observed/);
	});

	it("labels the axes with units (k, fractional DTE, IV %)", () => {
		render(<IvSurface surface={READY_SURFACE} />);

		const props = capturedProps.current as PlotProps;
		expect(props.layout.scene.xaxis.title.text).toMatch(/moneyness/i);
		expect(props.layout.scene.yaxis.title.text).toMatch(/DTE/);
		expect(props.layout.scene.zaxis.title.text).toMatch(/IV/);
	});
});
