import { useCallback, useState } from "react";
import { errorMessage, getConfig } from "@/api";
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
	/** Set when the most recent load attempt failed; cleared on success. */
	configError: string | null;
	reload: () => Promise<void>;
}

/**
 * Config is fetched on specific events only (mount, symbol change, refresh
 * settlement) -- never on a timer. See PRD 11.3.
 */
export function useConfig(): UseConfigResult {
	const [config, setConfig] = useState<ConfigResponse | null>(null);
	const [serverOffsetMs, setServerOffsetMs] = useState(0);
	const [configError, setConfigError] = useState<string | null>(null);

	const reload = useCallback(async () => {
		try {
			const result = await getConfig();
			setConfig(result);
			setServerOffsetMs(clockOffsetMs(result.server_time));
			setConfigError(null);
		} catch (error) {
			// PRD 11.3: "if it fails, retain the existing local cooldown and allow
			// a later manual attempt" -- leave prior config/offset state
			// untouched, but surface the failure instead of leaving the caller
			// stuck on an unexplained loading state with no recovery action.
			setConfigError(errorMessage(error));
		}
	}, []);

	return { config, serverOffsetMs, configError, reload };
}
