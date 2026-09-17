import { vi } from "vitest";
import type { ConfigResponse, DashboardResponse } from "./types";

export function makeConfig(
	overrides: Partial<ConfigResponse> = {},
): ConfigResponse {
	return {
		symbols: ["SPY", "QQQ", "AAPL"],
		default_symbol: "SPY",
		source_mode: "fixture",
		risk_free_rate: 0.04,
		dividend_yields: { SPY: 0, QQQ: 0, AAPL: 0 },
		min_calendar_dte: 1,
		max_calendar_dte: 60,
		min_strike_pct: 0.8,
		max_strike_pct: 1.2,
		refresh_mode: "manual",
		refresh_min_interval_seconds: 60,
		refresh_in_progress: false,
		server_time: new Date().toISOString(),
		refresh_not_before: null,
		...overrides,
	};
}

export function makeDashboard(
	overrides: Partial<DashboardResponse> = {},
): DashboardResponse {
	return {
		schema_version: 1,
		snapshot_id: "snap-1",
		symbol: "SPY",
		source_mode: "fixture",
		collected_at: new Date().toISOString(),
		valuation_at: new Date().toISOString(),
		chain_asof: null,
		spot_asof: null,
		oi_asof: null,
		spot: 550,
		spot_kind: "last_trade",
		spot_origin: "chain_payload",
		parameters: {
			r: 0.04,
			q: 0,
			multiplier_assumed: false,
			min_calendar_dte: 1,
			max_calendar_dte: 60,
			min_strike_pct: 0.8,
			max_strike_pct: 1.2,
			pricing_time_convention: "16:00 America/New_York on expiration date",
			algorithm_version: "1",
		},
		warnings: [],
		quality: {
			source_rows: 10,
			normalized_contracts: 20,
			in_scope_contracts: 20,
			valid_ivs: 15,
			known_oi_contracts: 18,
			complete_gex_cells: 5,
			exclusion_counts: {},
		},
		gex: {
			strikes: ["540.0", "550.0"],
			expirations: ["2026-01-15"],
			cells: [
				[
					{
						call_oi: 100,
						put_oi: 80,
						call_gamma: 0.02,
						put_gamma: 0.018,
						call_exposure: 200000,
						put_exposure: 120000,
						signed_proxy: 80000,
						gross_exposure: 320000,
						status: "COMPLETE",
					},
					null,
				],
			],
		},
		surface: {
			status: "INSUFFICIENT_DATA",
			k: [],
			expirations: [],
			dte: [],
			iv: null,
			observations: [],
		},
		...overrides,
	};
}

interface MockServerState {
	config: ConfigResponse;
	dashboards: Record<string, DashboardResponse | null>;
	onRefresh?: (symbol: string) =>
		| DashboardResponse
		| {
				error: {
					code: string;
					message: string;
					retry_after_seconds: number | null;
				};
		  };
}

/** Installs a fetch mock implementing the three real endpoints this app calls. */
export function installFetchMock(
	state: MockServerState,
): ReturnType<typeof vi.fn> {
	const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		const url = typeof input === "string" ? input : input.toString();
		const method = init?.method ?? "GET";

		if (url.endsWith("/api/config") && method === "GET") {
			return jsonResponse(200, state.config);
		}

		const dashboardMatch = url.match(/\/api\/dashboard\/([^/]+)$/);
		if (dashboardMatch && method === "GET") {
			const symbol = dashboardMatch[1];
			const dashboard = state.dashboards[symbol];
			if (dashboard === undefined || dashboard === null) {
				return jsonResponse(404, {
					error: {
						code: "NO_SNAPSHOT",
						message: "none",
						retry_after_seconds: null,
					},
				});
			}
			return jsonResponse(200, dashboard);
		}

		const refreshMatch = url.match(/\/api\/dashboard\/([^/]+)\/refresh$/);
		if (refreshMatch && method === "POST") {
			const symbol = refreshMatch[1];
			const result = state.onRefresh?.(symbol) ?? state.dashboards[symbol];
			if (result && "error" in result) {
				return jsonResponse(502, result);
			}
			if (result) {
				state.dashboards[symbol] = result;
				return jsonResponse(200, result);
			}
			return jsonResponse(502, {
				error: {
					code: "UPSTREAM_UNAVAILABLE",
					message: "no fixture",
					retry_after_seconds: null,
				},
			});
		}

		throw new Error(`Unhandled fetch in test: ${method} ${url}`);
	});
	vi.stubGlobal("fetch", fn);
	return fn;
}

export function jsonResponse(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}
