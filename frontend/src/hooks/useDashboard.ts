import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getDashboard, isAbortError, postRefresh } from "@/api";
import type { ApiErrorBody, DashboardResponse } from "@/types";

export type DashboardStatus = "loading" | "empty" | "ready";

export interface UseDashboardResult {
	dashboard: DashboardResponse | null;
	status: DashboardStatus;
	refreshing: boolean;
	refreshError: ApiErrorBody | null;
	refresh: () => Promise<void>;
}

/**
 * Loads the saved dashboard for `symbol` (GET only, zero provider calls) and
 * exposes a manual `refresh()` (the only POST in the app).
 *
 * A single "generation" counter, shared by the load effect and refresh(), is
 * the actual correctness guarantee for response ordering: starting a new
 * load (symbol change) or a new refresh bumps it, and a response may only
 * update dashboard/status if its own captured generation is still current.
 * AbortController cancellation on symbol change is kept as a network
 * optimization, not relied on alone -- it does nothing for a GET that a
 * same-symbol refresh() outraces, which is the case the generation guard
 * exists for.
 */
export function useDashboard(
	symbol: string,
	onRefreshSettled: () => Promise<void>,
): UseDashboardResult {
	const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
	const [status, setStatus] = useState<DashboardStatus>("loading");
	const [refreshing, setRefreshing] = useState(false);
	const [refreshError, setRefreshError] = useState<ApiErrorBody | null>(null);
	const generationRef = useRef(0);
	const loadAbortRef = useRef<AbortController | null>(null);

	useEffect(() => {
		const generation = ++generationRef.current;
		setDashboard(null);
		setStatus("loading");
		setRefreshError(null);

		if (symbol === "") {
			// Config hasn't resolved a default symbol yet; nothing to fetch.
			return;
		}

		const controller = new AbortController();
		loadAbortRef.current = controller;

		getDashboard(symbol, controller.signal)
			.then((result) => {
				if (generationRef.current !== generation) return; // superseded
				setDashboard(result);
				setStatus(result === null ? "empty" : "ready");
			})
			.catch((error: unknown) => {
				if (isAbortError(error)) return;
				if (generationRef.current !== generation) return;
				setStatus("empty");
			});

		return () => controller.abort();
	}, [symbol]);

	const refresh = useCallback(async () => {
		const generation = ++generationRef.current;
		loadAbortRef.current?.abort(); // optimization only; the generation check is the real guard
		setRefreshing(true);
		setRefreshError(null);
		try {
			const result = await postRefresh(symbol);
			if (generationRef.current === generation) {
				setDashboard(result);
				setStatus("ready");
			}
		} catch (error) {
			if (generationRef.current === generation) {
				const body: ApiErrorBody =
					error instanceof ApiError
						? {
								code: error.code,
								message: error.message,
								retry_after_seconds: error.retryAfterSeconds,
							}
						: {
								code: "UNKNOWN",
								message: "Refresh failed.",
								retry_after_seconds: null,
							};
				setRefreshError(body);
			}
		} finally {
			// Stay "refreshing" through the post-refresh config reconciliation
			// (the one GET that carries the updated cooldown), so a second
			// click can't slip in before the server's real state is reflected.
			await onRefreshSettled();
			setRefreshing(false);
		}
	}, [symbol, onRefreshSettled]);

	return { dashboard, status, refreshing, refreshError, refresh };
}
