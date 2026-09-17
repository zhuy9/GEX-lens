import { useCallback, useState } from "react";
import { getConfig } from "@/api";
import { clockOffsetMs } from "@/lib/time";
import type { ConfigResponse } from "@/types";

export interface UseConfigResult {
	config: ConfigResponse | null;
	/**
	 * Server-vs-browser clock offset, captured once when `config` was
	 * accepted. Do not recompute this from `config.server_time` elsewhere --
	 * `serverTime - Date.now()` collapses back to a fixed instant on every
	 * call, which freezes any age/cooldown text derived from it.
	 */
	serverOffsetMs: number;
	reload: () => Promise<void>;
}

/**
 * Config is fetched on specific events only (mount, symbol change, refresh
 * settlement) -- never on a timer. See PRD 11.3.
 */
export function useConfig(): UseConfigResult {
	const [config, setConfig] = useState<ConfigResponse | null>(null);
	const [serverOffsetMs, setServerOffsetMs] = useState(0);

	const reload = useCallback(async () => {
		try {
			const result = await getConfig();
			setConfig(result);
			setServerOffsetMs(clockOffsetMs(result.server_time));
		} catch {
			// PRD 11.3: "if it fails, retain the existing local cooldown and allow
			// a later manual attempt" -- leave prior config/offset state untouched.
		}
	}, []);

	return { config, serverOffsetMs, reload };
}
