import { useCallback, useEffect, useRef, useState } from "react";
import {
	ApiError,
	errorMessage,
	getDashboard,
	isAbortError,
	postRefresh,
} from "@/api";
import type { ApiErrorBody, DashboardResponse } from "@/types";

export type DashboardStatus = "loading" | "empty" | "ready" | "error";

export interface UseDashboardResult {
	dashboard: DashboardResponse | null;
	/**
	 * "empty" means the API's NO_SNAPSHOT outcome specifically -- no snapshot
	 * exists yet. Any other failure (network, 500, malformed body) is
	 * "error", not "empty": those are not the same fact, and showing "empty"
	 * for a real failure invites an unnecessary Refresh click.
	 */
	status: DashboardStatus;
	loadError: string | null;
	refreshing: boolean;
	refreshError: ApiErrorBody | null;
	refresh: () => Promise<void>;
	/** Retries the saved-dashboard GET only; never issues a POST. */
	retryLoad: () => void;
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
	const [loadError, setLoadError] = useState<string | null>(null);
	const [refreshing, setRefreshing] = useState(false);
	const [refreshError, setRefreshError] = useState<ApiErrorBody | null>(null);
	const generationRef = useRef(0);
	const loadAbortRef = useRef<AbortController | null>(null);
	const [reloadTick, setReloadTick] = useState(0);

	// reloadTick is intentionally unread inside the effect; it exists only to
	// force a re-run on demand (retryLoad).
	// biome-ignore lint/correctness/useExhaustiveDependencies: see above
	useEffect(() => {
		const generation = ++generationRef.current;
		setDashboard(null);
		setStatus("loading");
		setLoadError(null);
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
				setStatus("error");
				setLoadError(errorMessage(error));
			});

		return () => controller.abort();
	}, [symbol, reloadTick]);

	const retryLoad = useCallback(() => {
		setReloadTick((t) => t + 1);
	}, []);

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

	return {
		dashboard,
		status,
		loadError,
		refreshing,
		refreshError,
		refresh,
		retryLoad,
	};
}
