// PRD 11.3: countdown/age text is derived from the server clock offset, not
// assumed identical to the browser clock, and never itself makes a request.

export function clockOffsetMs(serverTimeIso: string): number {
	return new Date(serverTimeIso).getTime() - Date.now();
}

export function secondsUntil(
	deadlineIso: string | null,
	offsetMs: number,
): number {
	if (deadlineIso === null) return 0;
	const estimatedServerNow = Date.now() + offsetMs;
	const deadline = new Date(deadlineIso).getTime();
	return Math.ceil(Math.max(0, (deadline - estimatedServerNow) / 1000));
}

export function ageSeconds(collectedAtIso: string, offsetMs: number): number {
	const estimatedServerNow = Date.now() + offsetMs;
	const collectedAt = new Date(collectedAtIso).getTime();
	return Math.max(0, Math.floor((estimatedServerNow - collectedAt) / 1000));
}
