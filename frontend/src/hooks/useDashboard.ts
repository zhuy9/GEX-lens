import { useCallback, useEffect, useState } from "react";
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
 * exposes a manual `refresh()` (the only POST in the app). Switching `symbol`
 * cancels any in-flight GET for the previous symbol via the effect cleanup,
 * so a late response can never overwrite the newly selected symbol's view.
 */
export function useDashboard(
	symbol: string,
	onRefreshSettled: () => void,
): UseDashboardResult {
	const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
	const [status, setStatus] = useState<DashboardStatus>("loading");
	const [refreshing, setRefreshing] = useState(false);
	const [refreshError, setRefreshError] = useState<ApiErrorBody | null>(null);

	useEffect(() => {
		setDashboard(null);
		setStatus("loading");
		setRefreshError(null);

		if (symbol === "") {
			// Config hasn't resolved a default symbol yet; nothing to fetch.
			return;
		}

		const controller = new AbortController();

		getDashboard(symbol, controller.signal)
			.then((result) => {
				setDashboard(result);
				setStatus(result === null ? "empty" : "ready");
			})
			.catch((error: unknown) => {
				if (isAbortError(error)) return;
				setStatus("empty");
			});

		return () => controller.abort();
	}, [symbol]);

	const refresh = useCallback(async () => {
		setRefreshing(true);
		setRefreshError(null);
		try {
			const result = await postRefresh(symbol);
			setDashboard(result);
			setStatus("ready");
		} catch (error) {
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
		} finally {
			setRefreshing(false);
			onRefreshSettled();
		}
	}, [symbol, onRefreshSettled]);

	return { dashboard, status, refreshing, refreshError, refresh };
}
