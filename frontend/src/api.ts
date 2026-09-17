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
		const body = (await response.json()) as ApiErrorResponse;
		throw new ApiError(
			response.status,
			body.error.code,
			body.error.message,
			body.error.retry_after_seconds,
		);
	}
	return (await response.json()) as T;
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
	signal?: AbortSignal,
): Promise<DashboardResponse> {
	return request<DashboardResponse>(`/api/dashboard/${symbol}/refresh`, {
		method: "POST",
		signal,
	});
}
