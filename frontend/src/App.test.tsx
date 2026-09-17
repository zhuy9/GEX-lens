import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import {
	installFetchMock,
	jsonResponse,
	makeConfig,
	makeDashboard,
} from "./test-fixtures";

const { plotRenderCount } = vi.hoisted(() => ({ plotRenderCount: vi.fn() }));
vi.mock("react-plotly.js", () => ({
	default: (props: { data?: unknown[] }) => {
		plotRenderCount();
		return (
			<div
				data-testid="plotly-mock"
				data-trace-count={props.data?.length ?? 0}
			/>
		);
	},
}));

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
	plotRenderCount.mockClear();
});

function postCallCount(fetchMock: ReturnType<typeof vi.fn>): number {
	return fetchMock.mock.calls.filter(
		([, init]) => (init as RequestInit | undefined)?.method === "POST",
	).length;
}

describe("initial load", () => {
	it("shows an explicit empty state when no saved snapshot exists", async () => {
		installFetchMock({ config: makeConfig(), dashboards: {} });
		render(<App />);
		expect(
			await screen.findByText(/no saved snapshot yet/i),
		).toBeInTheDocument();
	});

	it("loads a saved snapshot without ever sending a POST", async () => {
		const dashboard = makeDashboard();
		const fetchMock = installFetchMock({
			config: makeConfig(),
			dashboards: { SPY: dashboard },
		});
		render(<App />);
		await screen.findByText("Snapshot");
		expect(postCallCount(fetchMock)).toBe(0);
	});
});

describe("load failures (R05)", () => {
	it("an initial config failure shows a retry alert, and retrying recovers", async () => {
		let configCallCount = 0;
		const dashboard = makeDashboard();
		const fetchMock = vi.fn(
			async (input: RequestInfo | URL, init?: RequestInit) => {
				const url = String(input);
				if (url.endsWith("/api/config")) {
					configCallCount += 1;
					if (configCallCount === 1) {
						return new Response("Internal Server Error", { status: 500 });
					}
					return jsonResponse(200, makeConfig());
				}
				if (url.endsWith("/api/dashboard/SPY") && init?.method !== "POST") {
					return jsonResponse(200, dashboard);
				}
				throw new Error(`Unhandled: ${url}`);
			},
		);
		vi.stubGlobal("fetch", fetchMock);

		const user = userEvent.setup();
		render(<App />);
		expect(
			await screen.findByText("Failed to load configuration"),
		).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /retry loading/i }));

		await screen.findByText("Snapshot"); // recovered
		expect(
			screen.queryByText("Failed to load configuration"),
		).not.toBeInTheDocument();
	});

	it("a saved-dashboard failure shows an error state distinct from empty, and retry sends no POST", async () => {
		const fetchMock = vi.fn(
			async (input: RequestInfo | URL, init?: RequestInit) => {
				const url = String(input);
				if (url.endsWith("/api/config")) return jsonResponse(200, makeConfig());
				if (url.endsWith("/api/dashboard/SPY") && init?.method !== "POST") {
					return new Response("Internal Server Error", { status: 500 });
				}
				throw new Error(`Unhandled: ${url}`);
			},
		);
		vi.stubGlobal("fetch", fetchMock);

		render(<App />);
		expect(
			await screen.findByText("Failed to load the saved snapshot"),
		).toBeInTheDocument();
		expect(
			screen.queryByText(/no saved snapshot yet/i),
		).not.toBeInTheDocument();

		const callsBefore = fetchMock.mock.calls.length;
		const user = userEvent.setup();
		await user.click(screen.getByRole("button", { name: /retry loading/i }));

		await waitFor(() =>
			expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore),
		);
		expect(postCallCount(fetchMock)).toBe(0); // retry is GET-only, never a POST
	});

	it("a malformed (non-JSON) error body still produces a bounded message, not a crash", async () => {
		const fetchMock = vi.fn(
			async (input: RequestInfo | URL, init?: RequestInit) => {
				const url = String(input);
				if (url.endsWith("/api/config")) return jsonResponse(200, makeConfig());
				if (url.endsWith("/api/dashboard/SPY") && init?.method !== "POST") {
					return new Response("<html>502 Bad Gateway</html>", { status: 502 });
				}
				throw new Error(`Unhandled: ${url}`);
			},
		);
		vi.stubGlobal("fetch", fetchMock);

		render(<App />);
		expect(
			await screen.findByText("Failed to load the saved snapshot"),
		).toBeInTheDocument();
		expect(
			screen.getByText(/request failed \(http 502\)/i),
		).toBeInTheDocument();
	});
});

describe("manual refresh", () => {
	it("one click sends exactly one POST and replaces the dashboard", async () => {
		const initial = makeDashboard({ snapshot_id: "snap-1", spot: 100 });
		const refreshed = makeDashboard({ snapshot_id: "snap-2", spot: 200 });
		const fetchMock = installFetchMock({
			config: makeConfig(),
			dashboards: { SPY: initial },
			onRefresh: () => refreshed,
		});
		const user = userEvent.setup();
		render(<App />);

		const button = await screen.findByRole("button", { name: /^refresh$/i });
		await user.click(button);

		await screen.findByText("$200.00");
		expect(postCallCount(fetchMock)).toBe(1);
	});

	it("repeated clicks while a refresh is pending produce only one POST", async () => {
		const initial = makeDashboard({ snapshot_id: "snap-1" });
		let resolveRefresh: (() => void) | undefined;
		const fetchMock = vi.fn(
			async (input: RequestInfo | URL, init?: RequestInit) => {
				const url = String(input);
				if (url.endsWith("/api/config")) return jsonResponse(200, makeConfig());
				if (url.endsWith("/api/dashboard/SPY") && init?.method !== "POST") {
					return jsonResponse(200, initial);
				}
				if (url.endsWith("/api/dashboard/SPY/refresh")) {
					await new Promise<void>((resolve) => {
						resolveRefresh = resolve;
					});
					return jsonResponse(200, makeDashboard({ snapshot_id: "snap-2" }));
				}
				throw new Error(`Unhandled: ${url}`);
			},
		);
		vi.stubGlobal("fetch", fetchMock);

		const user = userEvent.setup();
		render(<App />);
		const button = await screen.findByRole("button", { name: /^refresh$/i });
		await user.click(button);
		await user.click(button); // disabled while refreshing; must not queue a second POST
		await user.click(button);

		resolveRefresh?.();
		await waitFor(() => expect(postCallCount(fetchMock)).toBe(1));
	});

	it("a failed refresh keeps the old snapshot, shows an alert, and does not retry itself", async () => {
		const initial = makeDashboard({ snapshot_id: "snap-1", spot: 100 });
		const fetchMock = installFetchMock({
			config: makeConfig(),
			dashboards: { SPY: initial },
			onRefresh: () => ({
				error: {
					code: "UPSTREAM_UNAVAILABLE",
					message: "Refresh failed. The previous snapshot was not changed.",
					retry_after_seconds: null,
				},
			}),
		});
		const user = userEvent.setup();
		render(<App />);

		const button = await screen.findByRole("button", { name: /^refresh$/i });
		await user.click(button);

		expect(await screen.findByText("Refresh failed")).toBeInTheDocument();
		expect(screen.getByText("$100.00")).toBeInTheDocument(); // old snapshot still shown

		await new Promise((resolve) => setTimeout(resolve, 20));
		expect(postCallCount(fetchMock)).toBe(1); // no automatic retry
	});

	it("R04: a slower initial GET cannot overwrite a faster refresh POST for the same symbol", async () => {
		const staleGetResult = makeDashboard({
			snapshot_id: "stale-get",
			spot: 999,
		});
		const refreshedResult = makeDashboard({
			snapshot_id: "fresh-post",
			spot: 123,
		});
		let releaseInitialGet: (() => void) | undefined;

		const fetchMock = vi.fn(
			async (input: RequestInfo | URL, init?: RequestInit) => {
				const url = String(input);
				if (url.endsWith("/api/config")) return jsonResponse(200, makeConfig());
				if (url.endsWith("/api/dashboard/SPY") && init?.method !== "POST") {
					// Stays pending until explicitly released, simulating a GET
					// that started before Refresh was clicked but resolves after.
					return new Promise<Response>((resolve) => {
						releaseInitialGet = () =>
							resolve(jsonResponse(200, staleGetResult));
					});
				}
				if (url.endsWith("/api/dashboard/SPY/refresh")) {
					return jsonResponse(200, refreshedResult);
				}
				throw new Error(`Unhandled: ${url}`);
			},
		);
		vi.stubGlobal("fetch", fetchMock);

		const user = userEvent.setup();
		render(<App />);

		const button = await screen.findByRole("button", { name: /^refresh$/i });
		await user.click(button);
		await screen.findByText("$123.00"); // the POST's result is shown

		// Now let the slower, earlier GET resolve with stale data.
		releaseInitialGet?.();
		await new Promise((resolve) => setTimeout(resolve, 20));

		expect(screen.getByText("$123.00")).toBeInTheDocument();
		expect(screen.queryByText("$999.00")).not.toBeInTheDocument();
	});
});

describe("GEX mode", () => {
	it("changing mode makes zero network requests", async () => {
		const dashboard = makeDashboard();
		const fetchMock = installFetchMock({
			config: makeConfig(),
			dashboards: { SPY: dashboard },
		});
		const user = userEvent.setup();
		render(<App />);

		await screen.findByText(/GEX Heatmap/i);
		const callsBefore = fetchMock.mock.calls.length;

		await user.click(screen.getByRole("combobox", { name: /gex mode/i }));
		await user.click(await screen.findByText(/gross oi-weighted gamma/i));

		expect(fetchMock.mock.calls.length).toBe(callsBefore);
	});
});

describe("symbol switching", () => {
	it("switching clears the prior symbol immediately, and a late response for an abandoned symbol cannot overwrite the current view", async () => {
		const spy = makeDashboard({ symbol: "SPY", spot: 111 });
		const qqq = makeDashboard({ symbol: "QQQ", spot: 222 });
		let releaseQqqResponse: (() => void) | undefined;

		const fetchMock = vi.fn(
			async (input: RequestInfo | URL, init?: RequestInit) => {
				const url = String(input);
				if (url.endsWith("/api/config")) return jsonResponse(200, makeConfig());
				if (url.endsWith("/api/dashboard/SPY")) return jsonResponse(200, spy);
				if (url.endsWith("/api/dashboard/QQQ")) {
					// QQQ's response stays pending until the test explicitly releases it,
					// simulating it resolving *after* the user has already moved on. Like
					// a real fetch, it must reject if its AbortSignal fires meanwhile.
					return new Promise<Response>((resolve, reject) => {
						releaseQqqResponse = () => resolve(jsonResponse(200, qqq));
						init?.signal?.addEventListener("abort", () =>
							reject(new DOMException("Aborted", "AbortError")),
						);
					});
				}
				throw new Error(`Unhandled: ${url}`);
			},
		);
		vi.stubGlobal("fetch", fetchMock);

		const user = userEvent.setup();
		render(<App />);
		await screen.findByText("$111.00");

		const select = screen.getByRole("combobox", { name: /symbol/i });
		await user.click(select);
		await user.click(await screen.findByText("QQQ")); // starts a slow QQQ fetch

		// PRD 11.2: switching clears the prior symbol's data immediately.
		expect(screen.queryByText("$111.00")).not.toBeInTheDocument();

		// Switch back to SPY before QQQ's fetch resolves.
		await user.click(select);
		await user.click(await screen.findByText("SPY"));
		await screen.findByText("$111.00");

		// Now let the stale QQQ response resolve -- it must not clobber SPY's view.
		releaseQqqResponse?.();
		await new Promise((resolve) => setTimeout(resolve, 20));
		expect(screen.getByText("$111.00")).toBeInTheDocument();
		expect(screen.queryByText("$222.00")).not.toBeInTheDocument();
	});
});

describe("no automatic refresh", () => {
	it("advancing time and dispatching focus/online/visibility events triggers no network requests", async () => {
		vi.useFakeTimers();
		const dashboard = makeDashboard();
		const fetchMock = installFetchMock({
			config: makeConfig(),
			dashboards: { SPY: dashboard },
		});

		render(<App />);
		await vi.waitFor(() =>
			expect(screen.getByText("Snapshot")).toBeInTheDocument(),
		);

		const callsBefore = fetchMock.mock.calls.length;

		await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
		window.dispatchEvent(new Event("focus"));
		window.dispatchEvent(new Event("online"));
		document.dispatchEvent(new Event("visibilitychange"));
		await vi.advanceTimersByTimeAsync(1000);

		expect(fetchMock.mock.calls.length).toBe(callsBefore);
		vi.useRealTimers();
	});

	it("R01: countdown and snapshot age actually advance with the clock, and the button re-enables at the deadline", async () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date("2026-01-01T00:00:00.000Z"));

		const config = makeConfig({
			server_time: "2026-01-01T00:00:00.000Z",
			refresh_not_before: "2026-01-01T00:00:30.000Z",
		});
		const dashboard = makeDashboard({
			collected_at: "2026-01-01T00:00:00.000Z",
		});
		const fetchMock = installFetchMock({
			config,
			dashboards: { SPY: dashboard },
		});

		render(<App />);
		// vi.waitFor's own polling can silently advance the fake clock by a
		// sub-second, unpredictable amount before this first check passes, so
		// read the actual starting values instead of assuming "30s"/"0s ago".
		await vi.waitFor(() =>
			expect(
				screen.getByRole("button", { name: /refresh available in \d+s/i }),
			).toBeInTheDocument(),
		);
		const initialRemaining = Number(
			screen
				.getByRole("button", { name: /refresh available in \d+s/i })
				.textContent?.match(/(\d+)s/)?.[1],
		);
		const initialAge = Number(
			screen
				.getByText(/^Collected \d+s ago$/)
				.textContent?.match(/(\d+)s/)?.[1],
		);

		const callsBefore = fetchMock.mock.calls.length;

		await vi.advanceTimersByTimeAsync(20_000);
		const remainingAfter = Number(
			screen
				.getByRole("button", { name: /refresh available in \d+s/i })
				.textContent?.match(/(\d+)s/)?.[1],
		);
		const ageAfter = Number(
			screen
				.getByText(/^Collected \d+s ago$/)
				.textContent?.match(/(\d+)s/)?.[1],
		);
		// Exact digits are sensitive to fake-timer/React tick-scheduling lag
		// (a display-only concern); a 1-tick tolerance still cleanly tells a
		// genuinely advancing clock apart from R01's frozen one.
		expect(
			Math.abs(initialRemaining - 20 - remainingAfter),
		).toBeLessThanOrEqual(1);
		expect(Math.abs(initialAge + 20 - ageAfter)).toBeLessThanOrEqual(1);

		// Comfortably past the deadline regardless of the exact starting value.
		await vi.advanceTimersByTimeAsync((initialRemaining + 5) * 1000);
		expect(screen.getByRole("button", { name: /^refresh$/i })).toBeEnabled();

		expect(fetchMock.mock.calls.length).toBe(callsBefore);
		vi.useRealTimers();
	});

	it("R08: the chart subtree does not re-render on every display tick", async () => {
		vi.useFakeTimers();
		const dashboard = makeDashboard({
			surface: {
				status: "READY",
				k: [-0.1, 0, 0.1],
				expirations: ["2026-01-15"],
				dte: [14],
				iv: [[0.2, 0.25, 0.3]],
				observations: [],
			},
		});
		installFetchMock({ config: makeConfig(), dashboards: { SPY: dashboard } });

		render(<App />);
		await vi.waitFor(() =>
			expect(screen.getByTestId("plotly-mock")).toBeInTheDocument(),
		);

		const rendersBeforeTicks = plotRenderCount.mock.calls.length;
		expect(rendersBeforeTicks).toBeGreaterThan(0);

		await vi.advanceTimersByTimeAsync(60 * 1000); // 60 display ticks, snapshot unchanged

		expect(plotRenderCount.mock.calls.length).toBe(rendersBeforeTicks);
		vi.useRealTimers();
	});
});
