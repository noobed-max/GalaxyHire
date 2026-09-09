import { useCallback, useEffect, useState, createContext, useContext } from "react";
import type { Lead, View, ApiFetch } from "../../types";
import type { IngestionJob, IngestionStatus, IngestionStatusResponse } from "../../api/types";
import { ONBOARDING_KEY } from "../lib/leadUtils";

export type { IngestionJob, IngestionStatus, IngestionStatusResponse };

export const STAGE_LABELS: Record<1 | 2 | 3 | 4, string> = {
  1: "Reading document & extracting text...",
  2: "Analyzing experiences & projects with AI...",
  3: "Cross-referencing existing profile for duplicates...",
  4: "Indexing skills and tags...",
};

export const STAGE_KEY_TO_NUMBER: Record<string, 1 | 2 | 3 | 4> = {
  reading: 1,
  extracting_text: 1,
  extracting: 2,
  ai_analysis: 2,
  deduping: 3,
  checking_duplicates: 3,
  indexing: 4,
  indexing_tags: 4,
  completed: 4,
  done: 4,
};

const CART_KEY = "galaxyhire-cart-v1";

function readCart(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(CART_KEY) || "null");
    if (!Array.isArray(value)) return [];
    return [...new Set(value.filter((id): id is string => typeof id === "string" && id.length > 0))];
  } catch {
    return [];
  }
}

export function useAppShellState(api?: ApiFetch | null) {
  const [view, setView] = useState<View>("dashboard");
  const [sel, setSel] = useState<Lead | null>(null);
  // Apply cart: job_ids the user set aside for tailoring. localStorage-persisted (same
  // pattern as the active search) — the cart is a working set on this machine, not
  // server state, so it never touches the backend until generate/apply run per job.
  const [cart, setCart] = useState<string[]>(readCart);
  const [showSettings, setShowSettings] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(() => localStorage.getItem(ONBOARDING_KEY) !== "done");
  const [applyDraft, setApplyDraft] = useState("");
  const [applyAutoFocus, setApplyAutoFocus] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [reevaluating, setReevaluating] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [scanErr, setScanErr] = useState<string | null>(null);

  // Active Ingestion Task State (Milestone 1)
  const [activeIngestion, setActiveIngestion] = useState<IngestionJob | null>(null);

  // Global Polling Loop: preserves in-flight ingestion tracking across tab navigation
  useEffect(() => {
    if (!api || !activeIngestion || activeIngestion.status !== "processing") {
      return;
    }

    const taskId = activeIngestion.taskId;
    let cancelled = false;

    const pollStatus = async () => {
      try {
        const res = await api(`/api/v1/documents/ingest/status?task_id=${encodeURIComponent(taskId)}`);
        if (cancelled || !res.ok) return;
        const data: IngestionStatusResponse = await res.json();
        if (cancelled) return;

        setActiveIngestion(prev => {
          if (!prev || prev.taskId !== taskId) return prev;
          const stageNum = (data.stage_number || STAGE_KEY_TO_NUMBER[data.stage] || prev.stage || 1) as 1 | 2 | 3 | 4;
          const newStatus = data.status || prev.status;
          if (prev.status === "processing" && newStatus === "completed") {
            window.dispatchEvent(new CustomEvent("profile-refresh"));
          }
          return {
            ...prev,
            status: newStatus,
            stage: stageNum,
            stageLabel: data.stage_message || data.stage_label || STAGE_LABELS[stageNum] || prev.stageLabel,
            elapsedSeconds: typeof data.elapsed_seconds === "number"
              ? Math.round(data.elapsed_seconds)
              : Math.round((Date.now() - prev.startedAt) / 1000),
            thoughts: Array.isArray(data.thoughts) && data.thoughts.length ? data.thoughts : prev.thoughts,
            hasStagedDuplicates: Boolean(data.has_staged_duplicates),
            reviewableDuplicates: data.reviewable_duplicates || prev.reviewableDuplicates,
            error: data.error ?? prev.error,
            result: data.result ?? prev.result,
          };
        });
      } catch {
        // Tolerant to transient connection hiccups
      }
    };

    const interval = setInterval(pollStatus, 1000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [api, activeIngestion?.taskId, activeIngestion?.status]);

  const closeDrawer = useCallback(() => setSel(null), []);
  const addToCart = useCallback((jobId: string) => {
    if (!jobId) return;
    setCart(prev => {
      if (prev.includes(jobId)) return prev;
      const next = [...prev, jobId];
      try { localStorage.setItem(CART_KEY, JSON.stringify(next)); } catch { /* private mode: memory only */ }
      return next;
    });
  }, []);
  const removeFromCart = useCallback((jobId: string) => {
    setCart(prev => {
      if (!prev.includes(jobId)) return prev;
      const next = prev.filter(id => id !== jobId);
      try { localStorage.setItem(CART_KEY, JSON.stringify(next)); } catch { /* private mode: memory only */ }
      return next;
    });
  }, []);
  const clearCart = useCallback(() => {
    setCart([]);
    try { localStorage.removeItem(CART_KEY); } catch { /* private mode: memory only */ }
  }, []);
  const focusApplyView = useCallback(() => {
    setView("apply");
    setApplyAutoFocus(true);
  }, []);
  const openSettings = useCallback(() => setShowSettings(true), []);
  const openSetupGuide = useCallback(() => {
    localStorage.removeItem(ONBOARDING_KEY);
    setShowOnboarding(true);
  }, []);

  return {
    view,
    setView,
    sel,
    setSel,
    cart,
    addToCart,
    removeFromCart,
    clearCart,
    showSettings,
    setShowSettings,
    showOnboarding,
    setShowOnboarding,
    applyDraft,
    setApplyDraft,
    applyAutoFocus,
    setApplyAutoFocus,
    scanning,
    setScanning,
    reevaluating,
    setReevaluating,
    cleaning,
    setCleaning,
    scanErr,
    setScanErr,
    closeDrawer,
    focusApplyView,
    openSettings,
    openSetupGuide,
    activeIngestion,
    setActiveIngestion,
    activeIngestTask: activeIngestion,
    setActiveIngestTask: setActiveIngestion,
  };
}

export type AppShellState = ReturnType<typeof useAppShellState>;
export const AppContext = createContext<AppShellState | null>(null);

export function useAppContext(): AppShellState | null {
  return useContext(AppContext);
}
