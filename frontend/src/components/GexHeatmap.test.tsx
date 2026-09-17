import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { GexCell, GexData } from "@/types";
import { GexHeatmap } from "./GexHeatmap";

afterEach(() => cleanup());

const COMPLETE_CELL: GexCell = {
	call_oi: 100,
	put_oi: 80,
	call_gamma: 0.02,
	put_gamma: 0.018,
	call_exposure: 200_000,
	put_exposure: 120_000,
	signed_proxy: 80_000,
	gross_exposure: 320_000,
	status: "COMPLETE",
};

function makeGex(overrides: Partial<GexData> = {}): GexData {
	return {
		strikes: ["100.0"],
		expirations: ["2026-09-18"],
		cells: [[COMPLETE_CELL]],
		...overrides,
	};
}

describe("R10: displayed DTE uses the NY calendar date, not UTC", () => {
	it("shows 2d for an expiration 2 NY-calendar-days out, even when the UTC date is only 1 day out", () => {
		// 2026-09-17T02:00:00Z is 2026-09-16T22:00 EDT (UTC-4): NY is still
		// Sep 16, so Sep 18 is 2 NY-calendar-days away. A UTC-based diff
		// would see valuationAt's UTC date as Sep 17 and wrongly show 1d.
		render(
			<GexHeatmap
				gex={makeGex()}
				mode="signed"
				spot={100}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);
		expect(screen.getByText("2d")).toBeInTheDocument();
		expect(screen.queryByText("1d")).not.toBeInTheDocument();
	});
});

describe("R10: no usable GEX data is distinguished from no axes", () => {
	it("shows an explicit message when every cell is incomplete for the selected mode, without hiding the table", () => {
		const incompleteGex = makeGex({ cells: [[null]] });

		render(
			<GexHeatmap
				gex={incompleteGex}
				mode="signed"
				spot={100}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);

		expect(
			screen.getByText(/no complete gex cells for this mode/i),
		).toBeInTheDocument();
		// The table itself is still rendered (per-cell inspection retained).
		expect(screen.getByText("$100")).toBeInTheDocument(); // strike label
	});

	it("does not show the message when at least one complete cell exists", () => {
		render(
			<GexHeatmap
				gex={makeGex()}
				mode="signed"
				spot={100}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);
		expect(
			screen.queryByText(/no complete gex cells for this mode/i),
		).not.toBeInTheDocument();
	});
});

describe("R14: spot divider row", () => {
	it("appears between the two strikes bracketing the exact underlying price", () => {
		const gex = makeGex({
			strikes: ["95.0", "100.0", "105.0"],
			cells: [[COMPLETE_CELL, COMPLETE_CELL, COMPLETE_CELL]],
		});
		render(
			<GexHeatmap
				gex={gex}
				mode="signed"
				spot={101}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);

		const rows = screen.getAllByRole("row");
		const rowText = rows.map((r) => r.textContent ?? "");
		const spotIndex = rowText.findIndex((t) =>
			t.includes("Underlying $101.00"),
		);
		const idx105 = rowText.findIndex((t) => t.startsWith("$105"));
		const idx100 = rowText.findIndex((t) => t.startsWith("$100"));

		expect(spotIndex).toBeGreaterThan(-1);
		expect(idx105).toBeLessThan(spotIndex); // higher strike stays above the divider
		expect(spotIndex).toBeLessThan(idx100); // lower strike stays below it
	});
});

describe("R14: expand/collapse the strike window", () => {
	function makeManyStrikesGex(): GexData {
		const strikes = Array.from({ length: 25 }, (_, i) => `${90 + i}.0`);
		const cells = [strikes.map(() => COMPLETE_CELL)];
		return { strikes, expirations: ["2026-09-18"], cells };
	}

	it("shows a windowed subset by default, and all strikes after Expand", async () => {
		const user = userEvent.setup();
		render(
			<GexHeatmap
				gex={makeManyStrikesGex()}
				mode="signed"
				spot={100}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);

		expect(
			screen.getByText(/showing 17 of 25 strikes around spot/i),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /expand \(25 strikes\)/i }),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: /expand \(25 strikes\)/i }),
		);

		expect(screen.getByText(/showing 25 of 25 strikes/i)).toBeInTheDocument();
		expect(screen.queryByText(/around spot/i)).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /^collapse$/i }),
		).toBeInTheDocument();
	});
});

describe("R14: both GEX color modes", () => {
	it("signed mode shows put-heavy/call-heavy endpoints", () => {
		render(
			<GexHeatmap
				gex={makeGex()}
				mode="signed"
				spot={100}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);
		expect(screen.getByText(/Put-heavy/)).toBeInTheDocument();
		expect(screen.getByText(/Call-heavy/)).toBeInTheDocument();
	});

	it("gross mode shows a $0-to-high nonnegative scale instead", () => {
		render(
			<GexHeatmap
				gex={makeGex()}
				mode="gross"
				spot={100}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);
		expect(screen.queryByText(/Put-heavy/)).not.toBeInTheDocument();
		expect(screen.getByText("$0")).toBeInTheDocument();
		expect(screen.getByText(/High \(\$320K\)/)).toBeInTheDocument();
	});
});

describe("R10: units are visible on the panel", () => {
	it("shows the documented unit and numeric legend endpoints", () => {
		render(
			<GexHeatmap
				gex={makeGex()}
				mode="signed"
				spot={100}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);
		expect(
			screen.getByText(/USD delta-notional change per 1% underlying move/i),
		).toBeInTheDocument();
		expect(screen.getByText(/Call-heavy \(\$80K\)/)).toBeInTheDocument();
	});
});
