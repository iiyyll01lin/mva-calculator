import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ErrorBoundary] Uncaught error:', error, info.componentStack);
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  /**
   * Maps raw runtime error messages to IE-friendly guidance text.
   *
   * Priority rules (first match wins):
   *  1. Chunk-load failures — common after a new production build is deployed
   *     while the user still has the old page open.
   *  2. Property-access crashes on null/undefined — usually caused by stale
   *     localStorage data that is incompatible with the current schema.
   *  3. Fallback: show the raw error message as-is (already human-readable
   *     for errors thrown from the domain importers).
   */
  private friendlyMessage(): string {
    const raw = this.state.error?.message ?? '';
    if (/chunk|loading chunk|dynamically imported module|failed to fetch dynamically/i.test(raw)) {
      return 'A page module failed to load. Please refresh your browser (Ctrl + R / ⌘ R) and try again.';
    }
    if (/cannot read prop|undefined is not|null is not an object/i.test(raw)) {
      return (
        'A rendering error occurred — your saved data may be incompatible with this version of the app. ' +
        'Try importing a fresh Project JSON or clearing browser local storage, then reload.'
      );
    }
    return raw || 'An unexpected error occurred.';
  }

  override render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="error-boundary-card" role="alert">
          <h2>Something went wrong</h2>
          <p className="muted">{this.friendlyMessage()}</p>
          <button type="button" className="button secondary" onClick={this.handleReset}>
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
