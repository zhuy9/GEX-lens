import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartErrorBoundary } from "./ChartErrorBoundary";

afterEach(() => cleanup());

function Bomb({ shouldThrow }: { shouldThrow: boolean }) {
	if (shouldThrow) throw new Error("boom");
	return <div>ok</div>;
}

describe("R10: ChartErrorBoundary reset", () => {
	it("recovers when resetKey changes (e.g. a new snapshot loads), instead of staying broken forever", () => {
		// React logs the caught render error to the console; expected here.
		vi.spyOn(console, "error").mockImplementation(() => {});

		const { rerender } = render(
			<ChartErrorBoundary resetKey="snap-1">
				<Bomb shouldThrow={true} />
			</ChartErrorBoundary>,
		);
		expect(screen.getByText(/failed to render/i)).toBeInTheDocument();

		// Same resetKey, still broken: must not clear on an unrelated re-render.
		rerender(
			<ChartErrorBoundary resetKey="snap-1">
				<Bomb shouldThrow={true} />
			</ChartErrorBoundary>,
		);
		expect(screen.getByText(/failed to render/i)).toBeInTheDocument();

		// New snapshot arrives (new resetKey) and the child no longer throws.
		rerender(
			<ChartErrorBoundary resetKey="snap-2">
				<Bomb shouldThrow={false} />
			</ChartErrorBoundary>,
		);
		expect(screen.getByText("ok")).toBeInTheDocument();
		expect(screen.queryByText(/failed to render/i)).not.toBeInTheDocument();
	});

	it("uses a panel-appropriate message instead of always mentioning WebGL", () => {
		vi.spyOn(console, "error").mockImplementation(() => {});
		render(
			<ChartErrorBoundary resetKey="snap-1" message="Custom panel message">
				<Bomb shouldThrow={true} />
			</ChartErrorBoundary>,
		);
		expect(screen.getByText("Custom panel message")).toBeInTheDocument();
		expect(screen.queryByText(/webgl/i)).not.toBeInTheDocument();
	});
});
