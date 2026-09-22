import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { jsonResponse, makeConfig } from "@/test-fixtures";
import { useConfig } from "./useConfig";

afterEach(() => vi.unstubAllGlobals());

it.each([200, 500])(
	"ignores an older config response (HTTP %s)",
	async (status) => {
		let resolveOld!: (response: Response) => void;
		const oldResponse = new Promise<Response>((resolve) => {
			resolveOld = resolve;
		});
		const current = makeConfig({ refresh_not_before: "2026-01-02T21:01:00Z" });
		vi.stubGlobal(
			"fetch",
			vi
				.fn()
				.mockReturnValueOnce(oldResponse)
				.mockResolvedValueOnce(jsonResponse(200, current)),
		);
		const { result } = renderHook(() => useConfig());
		let oldLoad!: Promise<void>;
		await act(async () => {
			oldLoad = result.current.reload();
			await result.current.reload();
		});
		await act(async () => {
			resolveOld(jsonResponse(status, makeConfig()));
			await oldLoad;
		});
		expect(result.current.config).toEqual(current);
		expect(result.current.configError).toBeNull();
	},
);
