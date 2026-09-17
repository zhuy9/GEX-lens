import type {
	ApiErrorResponse,
	ConfigResponse,
	DashboardResponse,
} from "./types";

export class ApiError extends Error {
	status: number;
	code: string;
	retryAfterSeconds: number | null;

	constructor(
		status: number,
		code: string,
		message: string,
		retryAfterSeconds: number | null,
	) {
		super(message);
		this.name = "ApiError";
		this.status = status;
		this.code = code;
		this.retryAfterSeconds = retryAfterSeconds;
	}
}

export function isAbortError(error: unknown): boolean {
	return error instanceof DOMException && error.name === "AbortError";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await fetch(path, init);
	if (!response.ok) {
		// A failed response isn't guaranteed to carry the app's JSON error
		// envelope -- a proxy's HTML error page or a connection-level failure
		// can still reach here as a non-ok Response. Fall back to a bounded
		// generic error instead of throwing an unrelated JSON-parse error.
		let body: ApiErrorResponse | null = null;
		try {
			body = (await response.json()) as ApiErrorResponse;
		} catch {
			// not the expected JSON envelope
		}
		if (body?.error) {
			throw new ApiError(
				response.status,
				body.error.code,
				body.error.message,
				body.error.retry_after_seconds,
			);
		}
		throw new ApiError(
			response.status,
			"HTTP_ERROR",
			`Request failed (HTTP ${response.status})`,
			null,
		);
	}
	return (await response.json()) as T;
}

export function errorMessage(error: unknown): string {
	if (error instanceof ApiError) return error.message;
	if (error instanceof Error) return error.message.slice(0, 200);
	return "Unknown error";
}

export function getConfig(signal?: AbortSignal): Promise<ConfigResponse> {
	return request<ConfigResponse>("/api/config", { signal });
}

export async function getDashboard(
	symbol: string,
	signal?: AbortSignal,
): Promise<DashboardResponse | null> {
	try {
		return await request<DashboardResponse>(`/api/dashboard/${symbol}`, {
			signal,
		});
	} catch (error) {
		if (error instanceof ApiError && error.code === "NO_SNAPSHOT") {
			return null;
		}
		throw error;
	}
}

export function postRefresh(
	symbol: string,
	options?: { forceReferenceRefresh?: boolean; signal?: AbortSignal },
): Promise<DashboardResponse> {
	const query = options?.forceReferenceRefresh
		? "?force_reference_refresh=true"
		: "";
	return request<DashboardResponse>(
		`/api/dashboard/${symbol}/refresh${query}`,
		{ method: "POST", signal: options?.signal },
	);
}
