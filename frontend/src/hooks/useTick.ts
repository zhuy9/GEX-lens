import { useEffect, useState } from "react";

/**
 * A display-only 1-second counter for age/cooldown text (PRD 11.3).
 * Never makes a network request; stops on unmount.
 */
export function useTick(intervalMs = 1000): number {
	const [tick, setTick] = useState(0);

	useEffect(() => {
		const id = setInterval(() => setTick((t) => t + 1), intervalMs);
		return () => clearInterval(id);
	}, [intervalMs]);

	return tick;
}
