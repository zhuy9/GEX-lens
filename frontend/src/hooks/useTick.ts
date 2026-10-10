import { useEffect, useState } from "react";

/**
 * A display-only 1-second counter for age/cooldown text (PRD 11.3).
 * Never makes a network request; stops on unmount.
 */
export function useTick(): number {
	const [tick, setTick] = useState(0);

	useEffect(() => {
		const id = setInterval(() => setTick((t) => t + 1), 1000);
		return () => clearInterval(id);
	}, []);

	return tick;
}
