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
		canonical_unit: "usd_delta_notional_per_1pct",
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
				unit="per_1pct"
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
				unit="per_1pct"
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
				unit="per_1pct"
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
				unit="per_1pct"
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
		return makeGex({ strikes, expirations: ["2026-09-18"], cells });
	}

	it("shows a windowed subset by default, and all strikes after Expand", async () => {
		const user = userEvent.setup();
		render(
			<GexHeatmap
				gex={makeManyStrikesGex()}
				mode="signed"
				unit="per_1pct"
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
				unit="per_1pct"
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
				unit="per_1pct"
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
				unit="per_1pct"
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

// ADR-0001 M1/N5: S=200, gamma=0.02, multiplier=100, call OI=1000, put OI=600.
// Canonical storage stays per-1%-move (call 800,000 / put 480,000 / signed
// 320,000 / gross 1,280,000); per-$1 must show exactly half of that here
// because factor = 1 / (0.01 * 200) = 0.5.
const N5_CELL: GexCell = {
	call_oi: 1000,
	put_oi: 600,
	call_gamma: 0.02,
	put_gamma: 0.02,
	call_exposure: 800_000,
	put_exposure: 480_000,
	signed_proxy: 320_000,
	gross_exposure: 1_280_000,
	status: "COMPLETE",
};

describe("ADR-0001 M1: move-unit display conversion", () => {
	it("per 1% (default) shows the canonical stored magnitudes unchanged", () => {
		render(
			<GexHeatmap
				gex={makeGex({ cells: [[N5_CELL]] })}
				mode="signed"
				unit="per_1pct"
				spot={200}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);
		expect(screen.getByText("$320K")).toBeInTheDocument(); // signed proxy cell
		expect(screen.getByText(/Call-heavy \(\$320K\)/)).toBeInTheDocument();
		expect(screen.getByText(/per 1% underlying move/i)).toBeInTheDocument();
	});

	it("per $1 scales the displayed cell and legend bound by 1 / (0.01 * spot), using actual spot", () => {
		render(
			<GexHeatmap
				gex={makeGex({ cells: [[N5_CELL]] })}
				mode="signed"
				unit="per_1dollar"
				spot={200}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);
		expect(screen.getByText("$160K")).toBeInTheDocument(); // 320,000 * 0.5
		expect(screen.getByText(/Call-heavy \(\$160K\)/)).toBeInTheDocument();
		expect(screen.getByText(/per \$1 underlying move/i)).toBeInTheDocument();
	});

	it("switching units does not change gamma or OI, and gross mode scales the same way", () => {
		render(
			<GexHeatmap
				gex={makeGex({ cells: [[N5_CELL]] })}
				mode="gross"
				unit="per_1dollar"
				spot={200}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);
		expect(screen.getByText("$640K")).toBeInTheDocument(); // 1,280,000 * 0.5
		const cell = screen.getByText("$640K");
		expect(cell).toHaveAttribute(
			"title",
			expect.stringContaining("Call gamma: 0.02"),
		);
		expect(cell).toHaveAttribute(
			"title",
			expect.stringContaining("Call OI: 1,000"),
		);
	});

	it("switching units leaves status and relative color intensity unchanged", () => {
		// N5 (Section 14): "Changing units must leave gamma, OI, IV, spot,
		// eligibility, and relative color intensity unchanged." Two distinct
		// cells (different magnitudes) so `bound` is non-trivial, and value
		// and bound must scale by the identical factor for color to match.
		const smallCell: GexCell = { ...N5_CELL, signed_proxy: 40_000 };
		const gex = makeGex({
			strikes: ["195.0", "200.0"],
			cells: [[N5_CELL, smallCell]],
		});

		const { getAllByRole, rerender } = render(
			<GexHeatmap
				gex={gex}
				mode="signed"
				unit="per_1pct"
				spot={200}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);
		const cellsBefore = getAllByRole("cell").filter((c) => c.title);
		const before = cellsBefore.map((c) => ({
			background: c.style.backgroundColor,
			color: c.style.color,
			status: c.title.match(/Status: \w+/)?.[0],
		}));

		rerender(
			<GexHeatmap
				gex={gex}
				mode="signed"
				unit="per_1dollar"
				spot={200}
				valuationAt="2026-09-17T02:00:00Z"
			/>,
		);
		const cellsAfter = getAllByRole("cell").filter((c) => c.title);
		const after = cellsAfter.map((c) => ({
			background: c.style.backgroundColor,
			color: c.style.color,
			status: c.title.match(/Status: \w+/)?.[0],
		}));

		expect(after).toEqual(before);
		expect(before.some((c) => c.background !== "")).toBe(true); // non-vacuous
	});
});
