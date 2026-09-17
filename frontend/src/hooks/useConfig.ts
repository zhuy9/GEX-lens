import { useCallback, useState } from "react";
import { getConfig } from "@/api";
import type { ConfigResponse } from "@/types";

export interface UseConfigResult {
	config: ConfigResponse | null;
	reload: () => Promise<void>;
}

/**
 * Config is fetched on specific events only (mount, symbol change, refresh
 * settlement) -- never on a timer. See PRD 11.3.
 */
export function useConfig(): UseConfigResult {
	const [config, setConfig] = useState<ConfigResponse | null>(null);

	const reload = useCallback(async () => {
		try {
			const result = await getConfig();
			setConfig(result);
		} catch {
			// PRD 11.3: "if it fails, retain the existing local cooldown and allow
			// a later manual attempt" -- leave prior config state untouched.
		}
	}, []);

	return { config, reload };
}
