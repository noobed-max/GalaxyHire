/**
 * Warm every lazily-loaded view chunk right after the shell mounts.
 *
 * Every nav destination except Home sits behind React.lazy, so the first
 * click on each paid a dynamic-import round trip plus parse/execute of its
 * whole dependency subtree — visible as the "Opening…" spinner for seconds,
 * worst in dev where bundles are unminified. Importing the same modules here
 * (browser dedupes by URL) pulls every chunk into cache while the user reads
 * the dashboard, making later navigation instant.
 */

export function preloadViews(): void {
  const warm = () => {
    void import("../../features/dashboard/DashboardView");
    void import("../../features/apply/ApplyJobView");
    void import("../../features/pipeline/PipelineView");
    void import("../../features/pipeline/components/JobDetailsPanel");
    void import("../../features/pipeline/components/JobBuilders");
    void import("../../features/cart/CartView");
    void import("../../features/email/EmailMonitoringView");
    void import("../../features/activity/ActivityView");
    void import("../../features/analytics/AnalyticsView");
    void import("../../features/profile/ProfileView");
    void import("../../features/profile/IngestionView");
    void import("../../features/settings/SettingsModal");
  };
  // Yield past first paint and any startup fetch bursts before pulling MBs of JS.
  const ric = (window as { requestIdleCallback?: (cb: () => void) => number }).requestIdleCallback;
  if (typeof ric === "function") {
    window.setTimeout(() => ric(warm), 200);
  } else {
    window.setTimeout(warm, 1500);
  }
}
