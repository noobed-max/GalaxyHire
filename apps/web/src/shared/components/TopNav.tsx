import Icon from "./Icon";
import type { LeadCounts, View } from "../../types";
import { useAppVersion } from "../hooks/useAppVersion";

type NavItem = {
  id: View;
  label: string;
  icon: string;
  count?: keyof LeadCounts;
};

const PRIMARY_NAV: NavItem[] = [
  { id: "dashboard", label: "Home", icon: "home" },
  { id: "pipeline", label: "Find jobs", icon: "search", count: "total" },
  { id: "cart", label: "Apply cart", icon: "cart", count: "cart" },
  { id: "apply", label: "Tailor & apply", icon: "spark", count: "ready" },
  { id: "pipeline-applied", label: "Applications", icon: "check", count: "applied" },
  { id: "analytics", label: "Job Search Analytics", icon: "trending" },
  { id: "email", label: "Email updates", icon: "mail" },
  { id: "profile", label: "Your profile", icon: "user" },
];

const SECONDARY_NAV: NavItem[] = [
  { id: "ingestion", label: "Add experience", icon: "plus" },
  { id: "activity", label: "Activity log", icon: "pulse" },
];

function isActive(view: View, id: View) {
  if (id === "pipeline") {
    return view === "pipeline" || (
      view.startsWith("pipeline-") && view !== "pipeline-applied"
    );
  }
  return view === id;
}

function NavButton({
  item,
  view,
  leadCounts,
  setView,
}: {
  item: NavItem;
  view: View;
  leadCounts: LeadCounts;
  setView: (view: View) => void;
}) {
  const active = isActive(view, item.id);
  const count = item.count ? leadCounts[item.count] : 0;

  return (
    <button
      className={`gh-nav-item ${active ? "active" : ""}`}
      onClick={() => setView(item.id)}
      aria-label={item.label}
      aria-current={active ? "page" : undefined}
    >
      <span className="gh-nav-icon"><Icon name={item.icon} size={15} stroke={1.8} /></span>
      <span className="gh-nav-label">{item.label}</span>
      {Boolean(item.count && count) && <span className="gh-nav-count">{count}</span>}
    </button>
  );
}

export function TopNav({
  view,
  setView,
  leadCounts,
  onSettings,
}: {
  view: View;
  setView: (v: View) => void;
  leadCounts: LeadCounts;
  onSettings: () => void;
}) {
  const appVersion = useAppVersion();

  return (
    <header className="gh-topnav">
      <div className="gh-brand" title="GalaxyHire">
        <div className="gh-brand-mark"><Icon name="logo" size={30} /></div>
        <strong>GalaxyHire</strong>
      </div>

      <nav className="gh-nav gh-nav-primary" aria-label="Main navigation">
        {PRIMARY_NAV.map(item => (
          <NavButton
            key={item.id}
            item={item}
            view={view}
            leadCounts={leadCounts}
            setView={setView}
          />
        ))}
      </nav>

      <div className="gh-topnav-spacer" />

      <nav className="gh-nav gh-nav-secondary" aria-label="Additional tools">
        {SECONDARY_NAV.map(item => (
          <NavButton
            key={item.id}
            item={item}
            view={view}
            leadCounts={leadCounts}
            setView={setView}
          />
        ))}
      </nav>

      <div className="gh-topnav-divider" />

      <button
        className="gh-nav-item"
        onClick={onSettings}
        aria-label="Settings"
        title="Open settings"
      >
        <span className="gh-nav-icon"><Icon name="settings" size={15} /></span>
        <span className="gh-nav-label">Settings</span>
      </button>

      <span className="gh-topnav-version">v{appVersion}</span>
    </header>
  );
}
