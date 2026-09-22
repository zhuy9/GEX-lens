import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { PositioningProfile } from "./PositioningProfile";

const profiles = [
	{
		expiration: "2026-01-03",
		dte: 1,
		call_wall_strike: "105",
		call_wall_oi: 200,
		put_wall_strike: "95",
		put_wall_oi: 180,
		call_gex_peak_strike: "105",
		call_gex_peak: 120000,
		put_gex_peak_strike: "95",
		put_gex_peak: 90000,
		max_pain_strike: "100",
		max_pain_payout: 40000,
	},
	{
		expiration: "2026-01-10",
		dte: 8,
		call_wall_strike: "110",
		call_wall_oi: 300,
		put_wall_strike: "90",
		put_wall_oi: 250,
		call_gex_peak_strike: "110",
		call_gex_peak: 130000,
		put_gex_peak_strike: "90",
		put_gex_peak: 95000,
		max_pain_strike: "100",
		max_pain_payout: 50000,
	},
];

describe("PositioningProfile", () => {
	it("defaults to 1DTE and switches expiration", async () => {
		const user = userEvent.setup();
		render(<PositioningProfile profiles={profiles} />);

		expect(screen.getAllByText("$105.00")).not.toHaveLength(0);
		await user.selectOptions(screen.getByLabelText("Expiration"), "2026-01-10");
		expect(screen.getAllByText("$110.00")).not.toHaveLength(0);
	});
});
