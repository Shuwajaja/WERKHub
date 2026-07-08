import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Changing this key resets the boundary (e.g. on view switch). */
  resetKey?: string;
}

interface State {
  error: Error | null;
}

// React 19 has no functional error boundary; a class is required. This keeps a
// single crashing view from blanking the whole app (honest-degrade).
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface the failure rather than swallowing it.
    console.error("View crashed:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div role="alert" style={{ padding: 16, color: "var(--err)" }}>
          <p className="werk-label" style={{ color: "var(--err)" }}>
            View error
          </p>
          <p style={{ color: "var(--secondary)", fontSize: 13 }}>
            This view failed to render. {this.state.error.message}
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}
