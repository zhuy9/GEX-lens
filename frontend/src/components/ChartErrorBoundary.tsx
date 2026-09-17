import { Component, type ReactNode } from "react";

interface ChartErrorBoundaryProps {
	children: ReactNode;
}

interface ChartErrorBoundaryState {
	hasError: boolean;
}

/** Isolates a single chart panel's render failure (e.g. no WebGL) from the rest of the page. */
export class ChartErrorBoundary extends Component<
	ChartErrorBoundaryProps,
	ChartErrorBoundaryState
> {
	state: ChartErrorBoundaryState = { hasError: false };

	static getDerivedStateFromError(): ChartErrorBoundaryState {
		return { hasError: true };
	}

	render() {
		if (this.state.hasError) {
			return (
				<div className="flex h-96 items-center justify-center text-center text-sm text-muted-foreground">
					This panel failed to render. Your browser or GPU may not support
					WebGL.
				</div>
			);
		}
		return this.props.children;
	}
}
