import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeDashboard, makeDashboardV2 } from "@/test-fixtures";
import { PricingInputsPanel } from "./PricingInputsPanel";

afterEach(() => cleanup());

describe("ADR-0001 M4: v1 (legacy) dashboards", () => {
	it("shows the legacy-model disclosure and saved r/q, not a fabricated cash schedule", async () => {
		const user = userEvent.setup();
		const dashboard = makeDashboard();
		render(
			<PricingInputsPanel
				dashboard={dashboard}
				onForceRefresh={() => {}}
				disabled={false}
			/>,
		);
		await user.click(screen.getByText("Pricing inputs"));

		expect(
			screen.getByText(/Legacy continuous-yield model/i),
		).toBeInTheDocument();
		expect(screen.getByText(/r: 4\.0000%/)).toBeInTheDocument();
		expect(screen.queryByText("Cash schedule")).not.toBeInTheDocument();
	});
});

describe("ADR-0001 M4: v2 dashboards", () => {
	it("shows rate provenance, an empty reviewed schedule, and the force-refresh action", async () => {
		const user = userEvent.setup();
		const dashboard = makeDashboardV2();
		render(
			<PricingInputsPanel
				dashboard={dashboard}
				onForceRefresh={() => {}}
				disabled={false}
			/>,
		);
		await user.click(screen.getByText("Pricing inputs"));

		expect(screen.getByText(/Flat SOFR proxy/)).toBeInTheDocument();
		expect(screen.getByText(/r \(continuous\): 4\.0600%/)).toBeInTheDocument();
		expect(screen.getByText(/Raw quoted: 4\.0000%/)).toBeInTheDocument();
		expect(
			screen.getByText(/No dividend events in the reviewed coverage window/i),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", {
				name: /refresh including reference inputs/i,
			}),
		).toBeInTheDocument();
	});

	it("lists cash events with ex-date, amount, payment status, and estimated/declared/reported labels", async () => {
		const user = userEvent.setup();
		const dashboard = makeDashboardV2({
			market_inputs: {
				...makeDashboardV2().market_inputs,
				dividend_schedule: {
					review: makeDashboardV2().market_inputs.dividend_schedule.review,
					events: [
						{
							event_id: "e1",
							ex_date: "2026-02-05",
							payment_date: null,
							amount: "1.25",
							amount_status: "estimated",
							source_ref: "owner_review",
							source_provider_id: "owner_review",
						},
					],
				},
			},
		});
		render(
			<PricingInputsPanel
				dashboard={dashboard}
				onForceRefresh={() => {}}
				disabled={false}
			/>,
		);
		await user.click(screen.getByText("Pricing inputs"));

		expect(screen.getByText("2026-02-05")).toBeInTheDocument();
		expect(screen.getByText("$1.25")).toBeInTheDocument();
		expect(screen.getByText("assumed at ex-date")).toBeInTheDocument();
		expect(screen.getByText("Estimated")).toBeInTheDocument();
	});

	it("calls onForceRefresh when the button is clicked, and respects disabled", async () => {
		const user = userEvent.setup();
		const onForceRefresh = vi.fn();
		render(
			<PricingInputsPanel
				dashboard={makeDashboardV2()}
				onForceRefresh={onForceRefresh}
				disabled={false}
			/>,
		);
		await user.click(screen.getByText("Pricing inputs"));
		await user.click(
			screen.getByRole("button", {
				name: /refresh including reference inputs/i,
			}),
		);
		expect(onForceRefresh).toHaveBeenCalledTimes(1);
	});

	it("disables the force-refresh button when the global refresh gate is disabled", async () => {
		const user = userEvent.setup();
		render(
			<PricingInputsPanel
				dashboard={makeDashboardV2()}
				onForceRefresh={() => {}}
				disabled={true}
			/>,
		);
		await user.click(screen.getByText("Pricing inputs"));
		expect(
			screen.getByRole("button", {
				name: /refresh including reference inputs/i,
			}),
		).toBeDisabled();
	});
});
