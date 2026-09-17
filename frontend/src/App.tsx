import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { ChartErrorBoundary } from "@/components/ChartErrorBoundary";
import { GexHeatmap } from "@/components/GexHeatmap";
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
import { secondsUntil } from "@/lib/time";
import type { GexMode } from "@/types";

// Only this panel needs Plotly; the GEX heatmap is a plain HTML table. The
// production build is a single ~5MB (~1.5MB gzipped) bundle dominated by
// Plotly, so code-splitting it lets the rest of the page render without
// waiting for it.
const IvSurface = lazy(() =>
	import("@/components/IvSurface").then((m) => ({ default: m.IvSurface })),
);

export default function App() {
	const {
		config,
		serverOffsetMs,
		configError,
		reload: reloadConfig,
	} = useConfig();
	const [symbol, setSymbol] = useState<string | null>(null);
	const [gexMode, setGexMode] = useState<GexMode>("signed");
	const tick = useTick();

	// Returned (not fire-and-forgotten) so useDashboard can stay "refreshing"
	// until this reconciliation GET actually lands (R04): otherwise the
	// button re-enables before the updated cooldown is reflected.
	const handleRefreshSettled = useCallback(
		() => reloadConfig(),
		[reloadConfig],
	);

	const {
		dashboard,
		status,
		loadError,
		refreshing,
		refreshError,
		refresh,
		retryLoad,
	} = useDashboard(symbol ?? "", handleRefreshSettled);

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

	const cooldownRemaining = config
		? secondsUntil(config.refresh_not_before, serverOffsetMs)
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

			{config === null && configError === null && (
				<Skeleton className="h-10 w-full" />
			)}

			{configError !== null && (
				<Alert variant="destructive">
					<AlertTitle>
						{config === null
							? "Failed to load configuration"
							: "Failed to refresh configuration"}
					</AlertTitle>
					<AlertDescription className="flex flex-wrap items-center gap-2">
						<span>{configError}</span>
						<Button
							variant="outline"
							size="sm"
							onClick={() => void reloadConfig()}
						>
							Retry loading
						</Button>
					</AlertDescription>
				</Alert>
			)}

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

			{status === "error" && symbol !== null && (
				<Alert variant="destructive">
					<AlertTitle>Failed to load the saved snapshot</AlertTitle>
					<AlertDescription className="flex flex-wrap items-center gap-2">
						<span>{loadError}</span>
						<Button variant="outline" size="sm" onClick={retryLoad}>
							Retry loading
						</Button>
					</AlertDescription>
				</Alert>
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
								offsetMs={serverOffsetMs}
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
						<CardContent>
							<ChartErrorBoundary>
								<GexHeatmap
									gex={dashboard.gex}
									mode={gexMode}
									spot={dashboard.spot}
									valuationAt={dashboard.valuation_at}
								/>
							</ChartErrorBoundary>
						</CardContent>
					</Card>

					<Card>
						<CardHeader>
							<CardTitle>Implied Volatility Surface</CardTitle>
						</CardHeader>
						<CardContent className="h-96">
							<ChartErrorBoundary>
								<Suspense fallback={<Skeleton className="h-full w-full" />}>
									<IvSurface surface={dashboard.surface} />
								</Suspense>
							</ChartErrorBoundary>
						</CardContent>
					</Card>
				</>
			)}
		</div>
	);
}
