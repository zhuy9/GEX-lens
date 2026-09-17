// Mirrors backend/models.py's API-facing Pydantic models. Field shapes were
// confirmed against real /api responses, not guessed: strike values are
// Decimal-serialized as strings (precision), k/dte/iv are plain numbers.

// An opaque provider identity, not a closed enum: the backend's Settings/
// ConfigResponse/DashboardResponse.source_mode is a plain string so a test
// provider can carry its own configured identity end-to-end. "fixture" is
// the one value the UI special-cases (the synthetic-data banner).
export type SourceMode = string;

export interface ConfigResponse {
	symbols: string[];
	default_symbol: string;
	source_mode: SourceMode;
	risk_free_rate: number;
	dividend_yields: Record<string, number>;
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

export interface DashboardResponse {
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
