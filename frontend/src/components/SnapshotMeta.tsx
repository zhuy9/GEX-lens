import { AlertTriangle, Info } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { ageSeconds } from "@/lib/time";
import type { DashboardResponse } from "@/types";

interface SnapshotMetaProps {
	dashboard: DashboardResponse;
	offsetMs: number;
	/** Forces a re-render every second so age text stays current; unused otherwise. */
	tick: number;
}

const STALE_AFTER_SECONDS = 300;

const WARNING_TEXT: Record<string, string> = {
	MULTIPLIER_ASSUMED:
		"Contract multiplier (100 shares/contract) is assumed, not confirmed by the source.",
	VALUATION_TIME_ASSUMED:
		"No verified chain pricing timestamp; valuation uses collection start time instead.",
	TIMESTAMP_ALIGNMENT_UNKNOWN:
		"Chain and underlying-price timestamp alignment could not be established.",
	UNDERLYING_PRICE_CHANGED_DURING_COLLECTION:
		"The underlying price changed on a later page during collection; the first page price was kept.",
	NEAR_EX_DIVIDEND:
		"An included ex-date falls within the next 7 days -- a modeling caution, not a trade signal.",
	DIVIDEND_PAYMENT_TIME_ASSUMED_AT_EX:
		"A payment date is unknown for at least one event; discounted to its ex-date instead.",
	DIVIDEND_ALIGNMENT_UNVERIFIED:
		"The underlying price's timing relative to an ex-date could not be verified.",
	DIVIDEND_ESTIMATE_REPLACED:
		"A source-reported amount replaced an earlier estimate for at least one event.",
	DIVIDEND_AMOUNT_ESTIMATED:
		"At least one event's amount is an estimate, not a source-confirmed figure.",
};

export function formatTimestamp(value: string | null): string {
	if (value === null) return "Unknown";
	return new Date(value).toLocaleString();
}

/** Section 13: "Show the model and near-ex-date/timestamp warnings for both
 * analytical panels" -- a compact badge for the GEX heatmap/IV surface card
 * headers, distinct from SnapshotMeta's full per-warning Alert list above
 * (repeating that whole list on every panel would show each code 3x over). */
export function PanelWarnings({ warnings }: { warnings: string[] }) {
	if (warnings.length === 0) return null;
	const title = warnings.map((code) => WARNING_TEXT[code] ?? code).join("\n");
	return (
		<Badge variant="outline" title={title}>
			{warnings.length} warning{warnings.length === 1 ? "" : "s"}
		</Badge>
	);
}

export function SnapshotMeta({ dashboard, offsetMs, tick }: SnapshotMetaProps) {
	void tick;
	const age = ageSeconds(dashboard.collected_at, offsetMs);
	const isStale = age > STALE_AFTER_SECONDS;
	const { parameters, quality } = dashboard;

	return (
		<div className="flex flex-col gap-3 text-sm">
			{dashboard.source_mode === "fixture" && (
				<Alert variant="destructive">
					<AlertTriangle className="h-4 w-4" />
					<AlertTitle>SYNTHETIC DATA</AlertTitle>
					<AlertDescription>
						This snapshot is generated fixture data, not a real market
						observation.
					</AlertDescription>
				</Alert>
			)}

			<div className="flex flex-wrap items-center gap-2">
				<Badge variant="secondary">Source: {dashboard.source_mode}</Badge>
				<Badge variant={isStale ? "destructive" : "outline"}>
					Collected {age}s ago
				</Badge>
				<Badge variant="outline">
					Quality: {quality.valid_ivs}/{quality.normalized_contracts} priced
				</Badge>
			</div>

			{isStale && (
				<Alert>
					<AlertTriangle className="h-4 w-4" />
					<AlertDescription>Snapshot older than 5 minutes.</AlertDescription>
				</Alert>
			)}

			<dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
				<div>
					<dt className="text-muted-foreground">
						Underlying last (from chain)
					</dt>
					<dd>${dashboard.spot.toFixed(2)}</dd>
				</div>
				<div>
					<dt className="text-muted-foreground">Chain timestamp</dt>
					<dd>{formatTimestamp(dashboard.chain_asof)}</dd>
				</div>
				<div>
					<dt className="text-muted-foreground">Spot timestamp</dt>
					<dd>{formatTimestamp(dashboard.spot_asof)}</dd>
				</div>
				<div>
					<dt className="text-muted-foreground">r / q</dt>
					<dd>
						{(parameters.r * 100).toFixed(2)}% /{" "}
						{(parameters.q * 100).toFixed(2)}%
					</dd>
				</div>
				<div>
					<dt className="text-muted-foreground">DTE scope</dt>
					<dd>
						{parameters.min_calendar_dte}-{parameters.max_calendar_dte} days
					</dd>
				</div>
				<div>
					<dt className="text-muted-foreground">Strike scope</dt>
					<dd>
						{parameters.min_strike_pct}x-{parameters.max_strike_pct}x spot
					</dd>
				</div>
				<div>
					<dt className="text-muted-foreground">In-scope contracts</dt>
					<dd>
						{quality.in_scope_contracts} of {quality.normalized_contracts}
					</dd>
				</div>
				<div>
					<dt className="text-muted-foreground">Known-OI contracts</dt>
					<dd>{quality.known_oi_contracts}</dd>
				</div>
				<div>
					<dt className="text-muted-foreground">Complete GEX cells</dt>
					<dd>{quality.complete_gex_cells}</dd>
				</div>
			</dl>

			{dashboard.warnings.length > 0 && (
				<div className="flex flex-col gap-1">
					{dashboard.warnings.map((code) => (
						<Alert key={code}>
							<AlertTriangle className="h-4 w-4" />
							<AlertDescription>{WARNING_TEXT[code] ?? code}</AlertDescription>
						</Alert>
					))}
				</div>
			)}

			<Alert>
				<Info className="h-4 w-4" />
				<AlertDescription>
					BSM approximation. OI does not identify dealer positions. Surface
					interpolation is not arbitrage-free calibration.
				</AlertDescription>
			</Alert>
		</div>
	);
}
