import { Component, type ReactNode } from "react";

interface ChartErrorBoundaryProps {
	children: ReactNode;
	/**
	 * Changing this value (the active snapshot ID) clears a previous error
	 * and retries rendering the panel for the new snapshot. Without this, a
	 * failure permanently blanks the panel even after a successful refresh.
	 */
	resetKey: string;
	/** Panel-specific fallback text; a generic message otherwise, since not
	 * every wrapped panel is a WebGL chart (e.g. the GEX table isn't one). */
	message?: string;
}

interface ChartErrorBoundaryState {
	hasError: boolean;
}

/** Isolates a single chart panel's render failure from the rest of the page. */
export class ChartErrorBoundary extends Component<
	ChartErrorBoundaryProps,
	ChartErrorBoundaryState
> {
	state: ChartErrorBoundaryState = { hasError: false };

	static getDerivedStateFromError(): ChartErrorBoundaryState {
		return { hasError: true };
	}

	componentDidUpdate(prevProps: ChartErrorBoundaryProps): void {
		if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
			this.setState({ hasError: false });
		}
	}

	render() {
		if (this.state.hasError) {
			return (
				<div className="flex h-96 items-center justify-center text-center text-sm text-muted-foreground">
					{this.props.message ?? "This panel failed to render."}
				</div>
			);
		}
		return this.props.children;
	}
}
