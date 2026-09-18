import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { DashboardResponse, ResolvedDividend } from "@/types";
import { formatTimestamp } from "./SnapshotMeta";

interface PricingInputsPanelProps {
	dashboard: DashboardResponse;
	onForceRefresh: () => void;
	disabled: boolean;
}

const DIVIDEND_STATUS_LABEL: Record<ResolvedDividend["amount_status"], string> =
	{
		source_reported: "Source-reported",
		owner_declared: "Declared",
		estimated: "Estimated",
	};

function fmtPercent(value: number): string {
	return `${(value * 100).toFixed(4)}%`;
}

export function PricingInputsPanel({
	dashboard,
	onForceRefresh,
	disabled,
}: PricingInputsPanelProps) {
	if (dashboard.schema_version === 1) {
		return (
			<details className="text-sm">
				<summary className="cursor-pointer font-medium">Pricing inputs</summary>
				<div className="mt-2 flex flex-col gap-2">
					<Alert>
						<AlertDescription>
							Legacy continuous-yield model; reference provenance unavailable.
						</AlertDescription>
					</Alert>
					<div>
						r: {fmtPercent(dashboard.parameters.r)} · q:{" "}
						{fmtPercent(dashboard.parameters.q)}
					</div>
				</div>
			</details>
		);
	}

	const { market_inputs, pricing_contexts } = dashboard;
	const { rate, dividend_schedule } = market_inputs;
	const { review, events } = dividend_schedule;

	return (
		<details className="text-sm">
			<summary className="cursor-pointer font-medium">Pricing inputs</summary>
			<div className="mt-2 flex flex-col gap-3">
				<Alert>
					<AlertDescription>
						Cash-dividend PV BSM approximation. European model applied to
						American-style equity/ETF options; early exercise is not modeled.
					</AlertDescription>
				</Alert>

				<div className="flex flex-wrap items-center gap-2">
					<Badge variant="outline">
						{rate.normalization === "manual_already_continuous"
							? "Manual rate"
							: "Flat SOFR proxy"}
					</Badge>
					<span>r (continuous): {fmtPercent(rate.rate_cc)}</span>
					{rate.raw_percent_rate !== null && (
						<span>Raw quoted: {rate.raw_percent_rate.toFixed(4)}%</span>
					)}
					<span>Source: {rate.source_provider_id}</span>
					<span>Effective: {rate.effective_date}</span>
					<span>Retrieved: {formatTimestamp(rate.fetched_at)}</span>
				</div>

				<div>
					<div className="font-medium">Cash schedule</div>
					<div className="text-xs text-muted-foreground">
						Review coverage through {review.coverage_end} · reviewed{" "}
						{review.reviewed_at.slice(0, 10)}
					</div>
					{events.length === 0 ? (
						<div className="mt-1 text-xs text-muted-foreground">
							No dividend events in the reviewed coverage window.
						</div>
					) : (
						<table className="mt-1 w-full text-xs">
							<thead>
								<tr className="text-left text-muted-foreground">
									<th className="pr-2">Ex-date</th>
									<th className="pr-2">Amount</th>
									<th className="pr-2">Payment</th>
									<th className="pr-2">Source</th>
									<th>Status</th>
								</tr>
							</thead>
							<tbody>
								{events.map((e) => (
									<tr key={e.event_id}>
										<td className="pr-2">{e.ex_date}</td>
										<td className="pr-2">${e.amount}</td>
										<td className="pr-2">
											{e.payment_date ?? "assumed at ex-date"}
										</td>
										<td className="pr-2">{e.source_provider_id}</td>
										<td>{DIVIDEND_STATUS_LABEL[e.amount_status]}</td>
									</tr>
								))}
							</tbody>
						</table>
					)}
				</div>

				{pricing_contexts.length > 0 && (
					<details>
						<summary className="cursor-pointer text-xs underline underline-offset-2">
							Per-expiry model spot / dividend PV / forward
						</summary>
						<table className="mt-1 w-full text-xs">
							<thead>
								<tr className="text-left text-muted-foreground">
									<th className="pr-2">Expiry</th>
									<th className="pr-2">Model spot</th>
									<th className="pr-2">Dividend PV</th>
									<th className="pr-2">Forward</th>
									<th>Status</th>
								</tr>
							</thead>
							<tbody>
								{pricing_contexts.map((ctx) => (
									<tr key={ctx.expiration}>
										<td className="pr-2">{ctx.expiration}</td>
										<td className="pr-2">${ctx.model_spot.toFixed(2)}</td>
										<td className="pr-2">${ctx.pv_dividends.toFixed(2)}</td>
										<td className="pr-2">${ctx.forward.toFixed(2)}</td>
										<td>{ctx.status}</td>
									</tr>
								))}
							</tbody>
						</table>
					</details>
				)}

				{/* Model/dividend warnings render once, at the top of the
				Snapshot card (SnapshotMeta): app.py already merges these same
				codes into dashboard.warnings, so a second copy here would
				show every code twice. */}

				<Button
					variant="outline"
					size="sm"
					onClick={onForceRefresh}
					disabled={disabled}
					className="self-start"
				>
					Refresh including reference inputs
				</Button>
			</div>
		</details>
	);
}
