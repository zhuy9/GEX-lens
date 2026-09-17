import { useCallback, useEffect, useState } from "react";
import { ChartErrorBoundary } from "@/components/ChartErrorBoundary";
import { GexHeatmap } from "@/components/GexHeatmap";
import { IvSurface } from "@/components/IvSurface";
import { SnapshotMeta } from "@/components/SnapshotMeta";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useConfig } from "@/hooks/useConfig";
import { useDashboard } from "@/hooks/useDashboard";
import { useTick } from "@/hooks/useTick";
import { clockOffsetMs, secondsUntil } from "@/lib/time";
import type { GexMode } from "@/types";

export default function App() {
	const { config, reload: reloadConfig } = useConfig();
	const [symbol, setSymbol] = useState<string | null>(null);
	const [gexMode, setGexMode] = useState<GexMode>("signed");
	const tick = useTick();

	const handleRefreshSettled = useCallback(() => {
		void reloadConfig();
	}, [reloadConfig]);

	const { dashboard, status, refreshing, refreshError, refresh } = useDashboard(
		symbol ?? "",
		handleRefreshSettled,
	);

	// Open/reload: config resolves the default symbol once, which then
	// triggers useDashboard's own effect (PRD 11.3's "GET config, then GET
	// selected symbol's saved dashboard").
	useEffect(() => {
		void reloadConfig();
	}, [reloadConfig]);

	useEffect(() => {
		if (symbol === null && config !== null) {
			setSymbol(config.default_symbol);
		}
	}, [config, symbol]);

	function handleSymbolChange(next: string) {
		setSymbol(next);
		void reloadConfig();
	}

	const offsetMs = config ? clockOffsetMs(config.server_time) : 0;
	const cooldownRemaining = config
		? secondsUntil(config.refresh_not_before, offsetMs)
		: 0;
	void tick; // force recomputation of the above each second without a network call

	const symbolSelectorDisabled = refreshing;
	const refreshButtonDisabled =
		refreshing || cooldownRemaining > 0 || config === null;

	let refreshLabel = "Refresh";
	if (refreshing) refreshLabel = "Refreshing…";
	else if (cooldownRemaining > 0)
		refreshLabel = `Refresh available in ${cooldownRemaining}s`;

	return (
		<div className="mx-auto flex max-w-6xl flex-col gap-4 p-4">
			<header className="flex flex-wrap items-center justify-between gap-3">
				<h1 className="text-2xl font-semibold">GEX Lens</h1>
				<div className="flex flex-wrap items-center gap-2">
					<span className="text-sm text-muted-foreground" id="symbol-label">
						Symbol
					</span>
					<Select
						value={symbol ?? ""}
						onValueChange={handleSymbolChange}
						disabled={symbolSelectorDisabled || config === null}
					>
						<SelectTrigger aria-labelledby="symbol-label" className="w-28">
							<SelectValue placeholder="Loading…" />
						</SelectTrigger>
						<SelectContent>
							{config?.symbols.map((s) => (
								<SelectItem key={s} value={s}>
									{s}
								</SelectItem>
							))}
						</SelectContent>
					</Select>

					<Button
						onClick={() => void refresh()}
						disabled={refreshButtonDisabled}
					>
						{refreshLabel}
					</Button>
				</div>
			</header>

			{config === null && <Skeleton className="h-10 w-full" />}

			{refreshError && (
				<Alert variant="destructive">
					<AlertTitle>Refresh failed</AlertTitle>
					<AlertDescription>{refreshError.message}</AlertDescription>
				</Alert>
			)}

			{status === "loading" && (
				<div className="flex flex-col gap-4">
					<Skeleton className="h-32 w-full" />
					<Skeleton className="h-96 w-full" />
				</div>
			)}

			{status === "empty" && symbol !== null && (
				<Card>
					<CardContent className="py-8 text-center text-sm text-muted-foreground">
						No saved snapshot yet for {symbol}. Click Refresh to fetch one.
					</CardContent>
				</Card>
			)}

			{dashboard && (
				<>
					<Card>
						<CardHeader>
							<CardTitle>Snapshot</CardTitle>
						</CardHeader>
						<CardContent>
							<SnapshotMeta
								dashboard={dashboard}
								offsetMs={offsetMs}
								tick={tick}
							/>
						</CardContent>
					</Card>

					<div className="flex items-center gap-2">
						<span className="text-sm text-muted-foreground" id="gex-mode-label">
							GEX mode
						</span>
						<Select
							value={gexMode}
							onValueChange={(v) => setGexMode(v as GexMode)}
						>
							<SelectTrigger aria-labelledby="gex-mode-label" className="w-64">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="signed">Call-minus-put GEX proxy</SelectItem>
								<SelectItem value="gross">Gross OI-weighted gamma</SelectItem>
							</SelectContent>
						</Select>
						{dashboard.surface.status === "INSUFFICIENT_DATA" && (
							<Badge variant="outline">Surface: insufficient data</Badge>
						)}
					</div>

					<Card>
						<CardHeader>
							<CardTitle>GEX Heatmap</CardTitle>
						</CardHeader>
						<CardContent className="max-h-[70vh] overflow-y-auto">
							<ChartErrorBoundary>
								<GexHeatmap gex={dashboard.gex} mode={gexMode} />
							</ChartErrorBoundary>
						</CardContent>
					</Card>

					<Card>
						<CardHeader>
							<CardTitle>Implied Volatility Surface</CardTitle>
						</CardHeader>
						<CardContent className="h-96">
							<ChartErrorBoundary>
								<IvSurface surface={dashboard.surface} />
							</ChartErrorBoundary>
						</CardContent>
					</Card>
				</>
			)}
		</div>
	);
}
