// Mirrors backend/models.py's API-facing Pydantic models. Field shapes were
// confirmed against real /api responses, not guessed: strike values are
// Decimal-serialized as strings (precision), k/dte/iv are plain numbers.

// An opaque provider identity, not a closed enum: the backend's Settings/
// ConfigResponse/DashboardResponse.source_mode is a plain string so a test
// provider can carry its own configured identity end-to-end. "fixture" is
// the one value the UI special-cases (the synthetic-data banner).
export type SourceMode = string;

export interface ConfigResponse {
	config_schema_version: 2;
	symbols: string[];
	default_symbol: string;
	source_mode: SourceMode;
	pricing_model: string;
	rate_source: string;
	dividend_sources: Record<string, string>;
	default_move_unit: "per_1pct";
	min_calendar_dte: number;
	max_calendar_dte: number;
	min_strike_pct: number;
	max_strike_pct: number;
	refresh_mode: "manual";
	refresh_min_interval_seconds: number;
	refresh_in_progress: boolean;
	server_time: string;
	refresh_not_before: string | null;
}

export interface Parameters {
	r: number;
	q: number;
	multiplier_assumed: boolean;
	min_calendar_dte: number;
	max_calendar_dte: number;
	min_strike_pct: number;
	max_strike_pct: number;
	pricing_time_convention: string;
	algorithm_version: string;
}

export interface ParametersV2 {
	r: number;
	q: number;
	multiplier_assumed: boolean;
	min_calendar_dte: number;
	max_calendar_dte: number;
	min_strike_pct: number;
	max_strike_pct: number;
	pricing_time_convention: string;
	algorithm_version: "2";
	model_id: "cash_pv_bsm_v2";
	dividend_model: "cash_schedule";
}

export interface Instrument {
	symbol: string;
	instrument_class: "equity" | "etf";
	currency: "USD";
	exercise_style: "american";
	standard_multiplier: number;
}

export type RateNormalization =
	| "constant_daily_sofr_proxy"
	| "manual_already_continuous";

export interface ResolvedRate {
	source_provider_id: string;
	source_ref: string;
	effective_date: string;
	fetched_at: string;
	raw_percent_rate: number | null;
	rate_cc: number;
	quote_convention: "percent_simple_act360" | "continuous_act365f";
	normalization: RateNormalization;
	revision_indicator: string | null;
	manual_reason: string | null;
}

export type DividendAmountStatus =
	| "source_reported"
	| "owner_declared"
	| "estimated";

export interface ResolvedDividend {
	event_id: string;
	ex_date: string;
	payment_date: string | null;
	amount: string; // Decimal-as-string
	amount_status: DividendAmountStatus;
	source_ref: string;
	source_provider_id: string;
}

export interface ScheduleReview {
	symbol: string;
	reviewed_at: string;
	coverage_start: string;
	coverage_end: string;
	no_other_events_expected: true;
	source_refs: string[];
	expected_events: unknown[];
}

export interface ResolvedDividendSchedule {
	events: ResolvedDividend[];
	review: ScheduleReview;
}

export interface MarketInputs {
	input_schema_version: 1;
	resolved_at: string;
	rate: ResolvedRate;
	dividend_schedule: ResolvedDividendSchedule;
	warnings: string[];
	reference_bundle_hash: string;
}

export type PricingContextStatus = "OK" | "INVALID_DIVIDEND_ADJUSTED_SPOT";

export interface ExpiryPricingContext {
	expiration: string;
	valuation_at: string;
	expiry_at: string;
	T: number;
	actual_spot: number;
	model_spot: number;
	r_cc: number;
	q_continuous: number;
	pv_dividends: number;
	forward: number;
	used_event_ids: string[];
	warnings: string[];
	status: PricingContextStatus;
}

export interface QualityCounts {
	source_rows: number;
	normalized_contracts: number;
	in_scope_contracts: number;
	valid_ivs: number;
	known_oi_contracts: number;
	complete_gex_cells: number;
	exclusion_counts: Record<string, number>;
}

export type GexCellStatus = "COMPLETE" | "INCOMPLETE";

export interface GexCell {
	call_oi: number | null;
	put_oi: number | null;
	call_gamma: number | null;
	put_gamma: number | null;
	call_exposure: number | null;
	put_exposure: number | null;
	signed_proxy: number | null;
	gross_exposure: number | null;
	status: GexCellStatus;
}

export interface GexData {
	canonical_unit: "usd_delta_notional_per_1pct";
	strikes: string[]; // Decimal-as-string, ascending
	expirations: string[]; // ISO dates, ascending
	cells: (GexCell | null)[][]; // [expiration_index][strike_index]
}

export type SurfaceStatus = "READY" | "INSUFFICIENT_DATA";

export interface SurfaceObservation {
	expiration: string;
	strike: string; // Decimal-as-string
	k: number;
	dte: number;
	iv: number;
}

export interface SurfaceData {
	status: SurfaceStatus;
	k: number[]; // fixed 41-point moneyness grid
	expirations: string[]; // usable maturities only
	dte: number[]; // fractional days, parallel to expirations
	iv: (number | null)[][] | null; // [expiration_index][k_index]
	observations: SurfaceObservation[];
}

export interface DashboardResponseV1 {
	schema_version: 1;
	snapshot_id: string;
	symbol: string;
	source_mode: SourceMode;
	collected_at: string;
	valuation_at: string;
	chain_asof: string | null;
	spot_asof: string | null;
	oi_asof: string | null;
	spot: number;
	spot_kind: "last_trade";
	spot_origin: "chain_payload";
	parameters: Parameters;
	warnings: string[];
	quality: QualityCounts;
	gex: GexData;
	surface: SurfaceData;
}

export interface DashboardResponseV2 {
	schema_version: 2;
	snapshot_id: string;
	symbol: string;
	source_mode: SourceMode;
	collected_at: string;
	valuation_at: string;
	chain_asof: string | null;
	spot_asof: string | null;
	spot_asof_date: string | null;
	oi_asof: string | null;
	spot: number;
	spot_kind: "last_trade";
	spot_origin: "chain_payload";
	instrument: Instrument;
	parameters: ParametersV2;
	market_inputs: MarketInputs;
	pricing_contexts: ExpiryPricingContext[];
	warnings: string[];
	quality: QualityCounts;
	gex: GexData;
	surface: SurfaceData;
	calculation_input_hash: string;
}

// ADR-0001 Section 11.1: two explicit response variants, discriminated by
// schema_version. A GET returns whichever version was saved -- never
// upgraded, never recomputed.
export type DashboardResponse = DashboardResponseV1 | DashboardResponseV2;

export interface ApiErrorBody {
	code: string;
	message: string;
	retry_after_seconds: number | null;
}

export interface ApiErrorResponse {
	error: ApiErrorBody;
}

export type GexMode = "signed" | "gross";

export type MoveUnit = "per_1pct" | "per_1dollar";
