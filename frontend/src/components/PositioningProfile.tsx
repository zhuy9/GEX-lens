import { useEffect, useMemo, useState } from "react";
import type { PositioningProfile as PositioningProfileData } from "@/types";

interface PositioningProfileProps {
	profiles: PositioningProfileData[];
}

const EMPTY = "—";

function strike(value: string | null): string {
	return value === null ? EMPTY : `$${Number(value).toFixed(2)}`;
}

function oi(value: number | null): string {
	return value === null ? EMPTY : value.toLocaleString();
}

function dollars(value: number | null): string {
	return value === null
		? EMPTY
		: `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function nearestOneDte(profiles: PositioningProfileData[]): string {
	return (
		profiles.reduce(
			(best, profile) => {
				if (best === null) return profile;
				const distance = Math.abs(profile.dte - 1);
				const bestDistance = Math.abs(best.dte - 1);
				return distance < bestDistance ||
					(distance === bestDistance && profile.dte < best.dte)
					? profile
					: best;
			},
			null as PositioningProfileData | null,
		)?.expiration ?? ""
	);
}

export function PositioningProfile({ profiles }: PositioningProfileProps) {
	const defaultExpiration = useMemo(() => nearestOneDte(profiles), [profiles]);
	const [expiration, setExpiration] = useState(defaultExpiration);
	useEffect(() => setExpiration(defaultExpiration), [defaultExpiration]);

	const selected = profiles.find(
		(profile) => profile.expiration === expiration,
	);
	if (selected === undefined) return null;

	return (
		<div className="rounded-md border p-3 text-sm">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<div>
					<div className="font-medium">Expiration positioning</div>
					<div className="text-xs text-muted-foreground">
						OI walls, GEX peaks, and max pain are reference levels, not
						forecasts.
					</div>
				</div>
				<label className="flex items-center gap-2 text-xs">
					<span>Expiration</span>
					<select
						value={expiration}
						onChange={(event) => setExpiration(event.target.value)}
						className="rounded-md border bg-background px-2 py-1"
					>
						{profiles.map((profile) => (
							<option key={profile.expiration} value={profile.expiration}>
								{profile.expiration} · {profile.dte}DTE
							</option>
						))}
					</select>
				</label>
			</div>

			<div className="mt-3 grid gap-2 sm:grid-cols-3">
				<div className="rounded border p-2">
					<div className="text-xs text-muted-foreground">
						Call wall · raw OI
					</div>
					<div>{strike(selected.call_wall_strike)}</div>
					<div className="text-xs text-muted-foreground">
						{oi(selected.call_wall_oi)} contracts
					</div>
				</div>
				<div className="rounded border p-2">
					<div className="text-xs text-muted-foreground">Put wall · raw OI</div>
					<div>{strike(selected.put_wall_strike)}</div>
					<div className="text-xs text-muted-foreground">
						{oi(selected.put_wall_oi)} contracts
					</div>
				</div>
				<div className="rounded border p-2">
					<div className="text-xs text-muted-foreground">
						Max pain · holder payout
					</div>
					<div>{strike(selected.max_pain_strike)}</div>
					<div className="text-xs text-muted-foreground">
						{dollars(selected.max_pain_payout)}
					</div>
				</div>
				<div className="rounded border p-2">
					<div className="text-xs text-muted-foreground">
						Call GEX peak · per 1% move
					</div>
					<div>{strike(selected.call_gex_peak_strike)}</div>
					<div className="text-xs text-muted-foreground">
						{dollars(selected.call_gex_peak)}
					</div>
				</div>
				<div className="rounded border p-2">
					<div className="text-xs text-muted-foreground">
						Put GEX peak · per 1% move
					</div>
					<div>{strike(selected.put_gex_peak_strike)}</div>
					<div className="text-xs text-muted-foreground">
						{dollars(selected.put_gex_peak)}
					</div>
				</div>
			</div>
		</div>
	);
}
