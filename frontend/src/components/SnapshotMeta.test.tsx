import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { makeDashboardV2 } from "@/test-fixtures";
import { SnapshotMeta } from "./SnapshotMeta";

afterEach(() => cleanup());

// M4.5: "Frontend tests cover ... model limitations, near-ex-date warning,
// unknown timestamps." These disclosures are implemented in SnapshotMeta but
// had no dedicated test file before.

describe("ADR-0001 9.5: required model disclosures", () => {
	it("always shows the BSM-approximation/no-early-exercise disclosure", () => {
		render(
			<SnapshotMeta dashboard={makeDashboardV2()} offsetMs={0} tick={0} />,
		);
		expect(screen.getByText(/BSM approximation/i)).toBeInTheDocument();
		expect(
			screen.getByText(/does not identify dealer positions/i),
		).toBeInTheDocument();
	});

	it("shows the near-ex-date warning text for NEAR_EX_DIVIDEND, not just the raw code", () => {
		const dashboard = makeDashboardV2({ warnings: ["NEAR_EX_DIVIDEND"] });
		render(<SnapshotMeta dashboard={dashboard} offsetMs={0} tick={0} />);
		expect(
			screen.getByText(/modeling caution, not a trade signal/i),
		).toBeInTheDocument();
		expect(screen.queryByText("NEAR_EX_DIVIDEND")).not.toBeInTheDocument();
	});

	it("shows an unknown-timestamp label instead of a fabricated date", () => {
		const dashboard = makeDashboardV2({ chain_asof: null, spot_asof: null });
		render(<SnapshotMeta dashboard={dashboard} offsetMs={0} tick={0} />);
		expect(screen.getAllByText("Unknown")).toHaveLength(2); // chain + spot timestamp
	});

	it("falls back to the raw code for a warning with no known text, rather than hiding it", () => {
		const dashboard = makeDashboardV2({ warnings: ["SOME_FUTURE_CODE"] });
		render(<SnapshotMeta dashboard={dashboard} offsetMs={0} tick={0} />);
		expect(screen.getByText("SOME_FUTURE_CODE")).toBeInTheDocument();
	});
});
