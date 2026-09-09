import React from "react";

class ErrorBoundary extends React.Component<
  { children: React.ReactNode; label: string; api?: (path: string, opts?: RequestInit) => Promise<Response> },
  { error: Error | null; retryCount: number }
> {
  state = { error: null, retryCount: 0 };

  static getDerivedStateFromError(e: Error) {
    return { error: e };
  }

  componentDidCatch(e: Error, info: React.ErrorInfo) {
    console.error(`[ErrorBoundary:${this.props.label}]`, e, info);
    // A stale tab (open across a rebuild/redeploy) references chunk
    // filenames that no longer exist, so React.lazy rejects and lands here.
    // Reload once to pick up the fresh bundle instead of showing "failed to
    // load"; the sessionStorage guard prevents a reload loop if the new
    // build is also broken.
    const msg = String(e?.message ?? "");
    if (
      /dynamically imported module|loading chunk|error loading dynamically/i.test(msg) &&
      !sessionStorage.getItem("jhm-chunk-reload")
    ) {
      sessionStorage.setItem("jhm-chunk-reload", "1");
      window.location.reload();
      return;
    }
    this.props.api?.("/api/v1/errors", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        error: e.message,
        stack: e.stack,
        component: info.componentStack,
        label: this.props.label,
      }),
    }).catch(() => {});
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center",
          justifyContent: "center", height: "100%", gap: 12, opacity: 0.6,
        }}>
          <p style={{ margin: 0, fontSize: 14 }}>
            {this.props.label} failed to load.
          </p>
          <button onClick={() => this.setState(prev => ({ error: null, retryCount: prev.retryCount + 1 }))}
            style={{ fontSize: 12 }}>
            Retry
          </button>
        </div>
      );
    }
    return <React.Fragment key={this.state.retryCount}>{this.props.children}</React.Fragment>;
  }
}

export default ErrorBoundary;
