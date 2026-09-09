import { useEffect, useMemo, useState } from "react";
import Icon from "../../shared/components/Icon";
import type { ApiFetch, Lead } from "../../types";
import { getMark, leadDisplayHeading } from "../../shared/lib/leadUtils";
import { JobBuilders } from "../pipeline/components/JobBuilders";
import { ApplyHandoff } from "../apply/components/ApplyHandoff";

/**
 * Apply cart — jobs set aside for tailoring, one at a time.
 *
 * Left narrow column: the carted jobs (select to work on, remove to drop). Right: the
 * resume + cover letter builders for the selected job (the same JobBuilders the drawer
 * used to host) plus a per-job Apply button (ApplyHandoff: prepare, open portal, let
 * the extension fill). Nothing here reimplements generation or applying — it hosts the
 * existing per-job flows side by side.
 */
export function CartView({ api, leads, cart, removeFromCart }: {
  api: ApiFetch | null;
  leads: Lead[];
  cart: string[];
  removeFromCart: (jobId: string) => void;
}) {
  // Select the first item up front (static renderers don't run effects); the effect
  // below follows removals afterwards.
  const [selectedId, setSelectedId] = useState<string | null>(() => cart[0] ?? null);

  const byId = useMemo(() => new Map(leads.map(l => [l.job_id, l])), [leads]);
  const items = useMemo(
    () => cart.map(id => ({ id, lead: byId.get(id) ?? null })),
    [cart, byId],
  );

  // Keep a valid selection: first item by default, follow removals.
  useEffect(() => {
    if (selectedId && cart.includes(selectedId)) return;
    setSelectedId(cart[0] ?? null);
  }, [cart, selectedId]);

  const selected = selectedId ? byId.get(selectedId) ?? null : null;

  if (!api) return null;

  return (
    <main className="gh-page gh-cart scroll">
      <div className="gh-section-heading" style={{ marginBottom: 12 }}>
        <div>
          <span className="gh-kicker">Apply cart</span>
          <h2>{cart.length ? `${cart.length} job${cart.length === 1 ? "" : "s"} set aside` : "Your cart is empty"}</h2>
          <p>Pick a job on the left, tailor its resume and cover letter, then apply — one job at a time.</p>
        </div>
      </div>

      {cart.length === 0 ? (
        <div className="gh-empty-compact">
          <Icon name="cart" size={20} />
          <p>Add jobs from the pipeline or dashboard with <b>Add to cart</b>.</p>
        </div>
      ) : (
        <div className="gh-cart-grid">
          <aside className="gh-cart-list" aria-label="Carted jobs">
            {items.map(({ id, lead }) => {
              const active = id === selectedId;
              const heading = lead ? leadDisplayHeading(lead) : null;
              return (
                <div
                  key={id}
                  role="button"
                  tabIndex={0}
                  aria-pressed={active}
                  onClick={() => setSelectedId(id)}
                  onKeyDown={e => { if (e.key === "Enter" || e.key === " ") setSelectedId(id); }}
                  className={`gh-cart-item${active ? " active" : ""}`}
                >
                  <span className="gh-company-mark">{getMark(heading?.company ?? "?")}</span>
                  <span className="gh-cart-item-copy">
                    <strong>{heading ? heading.role : "Job no longer saved"}</strong>
                    <small>{heading ? heading.company : id}</small>
                  </span>
                  <button
                    className="btn btn-icon danger"
                    title="Remove from cart"
                    aria-label={`Remove ${heading?.role ?? id} from cart`}
                    onClick={e => { e.stopPropagation(); removeFromCart(id); }}
                  >
                    <Icon name="x" size={12} />
                  </button>
                </div>
              );
            })}
          </aside>

          <section className="gh-cart-main">
            {!selected ? (
              <div className="gh-empty-compact">
                <Icon name="file" size={20} />
                <p>This job is no longer in your saved list. Remove it from the cart to dismiss.</p>
              </div>
            ) : (
              <div key={selected.job_id} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <JobBuilders j={selected} api={api} />
                <ApplyHandoff api={api} jobId={selected.job_id} alreadyApplied={selected.status === "applied"} />
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
