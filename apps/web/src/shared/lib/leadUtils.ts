import type { Lead, LeadSort, SeniorityFilter } from "../../types";

export const getMark = (company: string) => company ? company.charAt(0).toUpperCase() : "?";
export const PAGE_SIZE = 80;
export const ONBOARDING_KEY = "justhireme:onboarding:v4";

export const leadSignal = (lead: Lead) => Math.max(lead.signal_score || 0, lead.score || 0);

export const leadSearchText = (lead: Lead) => [
  lead.title, lead.company, lead.platform, lead.status, lead.kind, lead.budget,
  lead.location, lead.urgency, lead.feedback, lead.description, lead.reason,
  lead.signal_reason, lead.learning_reason, ...(lead.signal_tags || []), ...(lead.tech_stack || []),
].join(" ").toLowerCase();

export const uniqueLeadValues = (leads: Lead[], key: keyof Lead) =>
  Array.from(new Set(leads.map(l => String(l[key] || "").trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b));

export const normalizeSeniority = (value: unknown): SeniorityFilter => {
  const raw = String(value || "").toLowerCase().trim();
  if (raw === "fresher" || raw === "freshers" || raw === "intern" || raw === "internship" || raw === "new grad") return "fresher";
  if (raw === "junior" || raw === "jr" || raw === "entry" || raw === "entry level") return "junior";
  if (raw === "mid" || raw === "middle" || raw === "mid-level" || raw === "mid level") return "mid";
  if (raw === "senior" || raw === "sr" || raw === "lead" || raw === "staff" || raw === "principal") return "senior";
  // The corpus's Seniority enum also emits `exec`. Without this it fell through to the title
  // heuristic below, so a VP role could end up labelled from a stray word instead of the level the
  // corpus had already resolved. The UI's four bands are coarser than the corpus's six, and exec
  // collapsing into senior is the honest mapping — both mean "well above entry".
  if (raw === "exec" || raw === "executive" || raw === "director" || raw === "vp") return "senior";
  return "unknown";
};

export const leadSeniority = (lead: Lead): SeniorityFilter => {
  const fromMeta = normalizeSeniority(lead.seniority_level || lead.source_meta?.seniority_level || lead.source_meta?.seniority);
  if (fromMeta !== "unknown") return fromMeta;

  // Fallback only, for leads the corpus could not label. The corpus resolves seniority properly in
  // galaxy/ingestion/levels.py — ladder codes (L5, SDE II, IC4), regional wording, negations,
  // resolution by specificity — and this must not contradict it. Two rules stop it lying:
  //
  //   1. **Title only.** This used to scan the description as well, which is the exact defect that
  //      was measured and removed from the backend: 741 of 1,343 `lead` labels came from
  //      descriptions merely containing "lead" as a verb or "staff"/"architect" in passing. "You
  //      will lead projects" is not a lead role; "reporting to the Director" is not an exec one.
  //      Reading years out of the description was the same mistake — a stray "5 years" in a
  //      benefits paragraph promoted junior postings to senior.
  //   2. **An explicit word beats a trailing numeral.** "Senior Engineer I" is senior. Matching
  //      `engineer i` first made it junior, which is how senior roles slipped past a Beginner
  //      filter — the bug that prompted this.
  const title = String(lead.title || "").toLowerCase();
  if (/\b(intern|internship|working student|werkstudent|praktikum|trainee|stagiaire)\b/.test(title)) return "fresher";
  if (/\b(chief|c[tefoi]o|vice president|vp|head of|director|principal|staff|distinguished|fellow|architect)\b/.test(title)) return "senior";
  if (/\b(senior|snr\.?|sr\.?|lead)\b/.test(title)) return "senior";
  if (/\b(mid[- ]?level|intermediate|engineer ii|developer ii|sde ii|sde 2)\b/.test(title)) return "mid";
  if (/\b(fresher|new grad(uate)?|campus)\b/.test(title)) return "fresher";
  if (/\b(junior|jr\.?|entry[- ]?level|associate|apprentice|early career|engineer i|developer i|sde i)\b/.test(title)) return "junior";
  return "unknown";
};

export const seniorityLabel = (level: SeniorityFilter) => ({
  fresher: "Fresher",
  junior: "Junior",
  mid: "Mid",
  senior: "Senior",
  beginner: "Beginner",
  unknown: "Unknown",
  all: "All levels",
}[level]);

export const seniorityTone = (level: SeniorityFilter) => ({
  fresher: "teal",
  junior: "green",
  mid: "yellow",
  senior: "purple",
  beginner: "green",
  unknown: "blue",
  all: "blue",
}[level]);

export const seniorityMatches = (lead: Lead, filter: SeniorityFilter) => {
  if (filter === "all") return true;
  const level = leadSeniority(lead);
  if (filter === "beginner") return level === "fresher" || level === "junior";
  return level === filter;
};

const normalizeLocation = (value: unknown) =>
  String(value || "").toLowerCase().replace(/\bbangalore\b/g, "bengaluru").replace(/\s+/g, " ").trim();

export const locationMatches = (actual: unknown, wanted: unknown) => {
  const where = normalizeLocation(actual);
  const requested = String(wanted || "").split(",").map(normalizeLocation).filter(Boolean);
  if (!requested.length) return true;
  if (where.includes("remote")) return true;
  return requested.every(part => where.includes(part));
};

export const cleanLeadText = (value: unknown) =>
  String(value || "").replace(/\s+/g, " ").trim();

export const isUrlOnlyText = (value: unknown) => {
  const text = cleanLeadText(value);
  if (!text) return false;
  return text.replace(/https?:\/\/\S+|www\.\S+/gi, "").replace(/[\s|,;:()[\]{}\-_/]+/g, "") === "";
};

export const roleFromUrl = (value: unknown) => {
  try {
    const raw = cleanLeadText(value);
    const parsed = new URL(raw.startsWith("http") ? raw : `https://${raw}`);
    const part = parsed.pathname.split("/").filter(Boolean).pop() || "";
    const cleaned = decodeURIComponent(part).replace(/^\d+[-_]+/, "").replace(/[-_]+/g, " ").replace(/\b[a-f0-9]{8,}\b/gi, "").trim();
    return cleaned.split(/\s+/).filter(Boolean).map(word =>
      ["ai", "ml", "llm", "nlp", "ui", "ux", "qa"].includes(word.toLowerCase()) ? word.toUpperCase() : word.replace(/\b\w/g, ch => ch.toUpperCase()),
    ).join(" ");
  } catch {
    return "";
  }
};

export const stripCompanyPrefix = (title: string, company: string) => {
  if (!title || !company) return title;
  const escaped = company.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return title.replace(new RegExp(`^${escaped}\\s*(?:[|:-]+)\\s*`, "i"), "").trim();
};

export const isLocationLike = (part: string) =>
  /\b(remote|onsite|on-site|hybrid|san francisco|new york|nyc|toronto|canada|india|usa|united states|europe|london|berlin|office|flexibility)\b/i.test(part) ||
  /^[A-Z][A-Za-z .-]+,\s*[A-Z]{2,}$/i.test(part);

export const isCompLike = (part: string, company: string) => {
  const normalized = part.toLowerCase().replace(/^www\./, "");
  const co = company.toLowerCase().replace(/^www\./, "");
  return Boolean(
    normalized === co ||
    normalized === `${co}.com` ||
    normalized.includes(`${co}.com`) ||
    (co && normalized.includes(co) && normalized.length <= co.length + 8)
  );
};

export const cleanRoleSegment = (segment: string) => {
  let role = cleanLeadText(segment);
  const looking = role.match(/\b(?:looking for|hiring(?: for)?|we are hiring|we're hiring)\s*:?\s*(?:a|an|two|[0-9]+)?\s*([^.;|]+)/i);
  if (looking?.[1]) role = cleanLeadText(looking[1]);
  role = role.replace(/\s+(?:to join|to help|for our|in our|at our)\b[\s\S]*$/i, "").trim();
  role = role.replace(/\s+\$[\s\S]*$/i, "").trim();
  role = role.replace(/\s+(?:we are hiring|we're hiring|looking for|hiring)\b[\s\S]*$/i, "").trim();
  role = role.replace(/\s+[–—-]\s*(?:we'?re|we are|looking|hiring)\b[\s\S]*$/i, "").trim();
  return role;
};

export const roleFromLead = (lead: Lead) => {
  const company = cleanLeadText(lead.company) || "Unknown company";
  const rawTitle = isUrlOnlyText(lead.title) ? roleFromUrl(lead.url || lead.title) || "Untitled role" : cleanLeadText(lead.title);
  const parts = rawTitle.split(/\s*\|\s*/).map(cleanLeadText).filter(Boolean);
  const roleHints = /\b(engineer|developer|designer|product|backend|front[- ]?end|frontend|full[- ]?stack|ai|ml|data|software|devops|sre|mobile|ios|android|platform|founding|deployed|research|intern|analyst|architect|security|qa)\b/i;
  const noisy = (part: string) =>
    isCompLike(part, company) ||
    isLocationLike(part) ||
    /^\$|₹|€|£/.test(part) ||
    /\b(equity|salary|visa|remote|onsite|hybrid)\b/i.test(part);
  const candidates = parts.map(cleanRoleSegment).filter(part => part && !noisy(part));
  const hinted = candidates.find(part => roleHints.test(part));
  const fallback = cleanRoleSegment(stripCompanyPrefix(rawTitle, company));
  const role = cleanLeadText(hinted || candidates[0] || fallback || "Untitled role");
  return role.length > 96 ? `${role.slice(0, 93).trim()}...` : role;
};

export const leadDisplayHeading = (lead: Lead) => {
  const company = cleanLeadText(lead.company) || "Unknown company";
  return { role: roleFromLead(lead), company };
};

export const sortLeads = (items: Lead[], sort: LeadSort) => {
  const copy = [...items];
  if (sort === "signal") return copy.sort((a, b) => (b.signal_score || 0) - (a.signal_score || 0));
  if (sort === "match") return copy.sort((a, b) => (b.score || 0) - (a.score || 0));
  if (sort === "company") return copy.sort((a, b) => `${a.company} ${a.title}`.localeCompare(`${b.company} ${b.title}`));
  if (sort === "recommended") {
    return copy.sort((a, b) => {
      const aContacted = a.last_contacted_at ? 1 : 0;
      const bContacted = b.last_contacted_at ? 1 : 0;
      return (
        leadSignal(b) - leadSignal(a) ||
        (b.learning_delta || 0) - (a.learning_delta || 0) ||
        bContacted - aContacted ||
        (b.budget ? 1 : 0) - (a.budget ? 1 : 0)
      );
    });
  }
  return copy;
};

export const getTone = (status: string) => {
  switch (status) {
    case "discovered":   return "blue";
    case "evaluating":   return "yellow";
    case "tailoring":    return "purple";
    case "approved":     return "green";
    case "applied":      return "orange";
    case "interviewing": return "pink";
    case "rejected":     return "red";
    case "accepted":     return "teal";
    case "discarded":    return "red";
    case "matched":      return "green";
    case "bidding":      return "teal";
    case "proposal_sent": return "purple";
    case "awarded":      return "blue";
    case "completed":    return "green";
    default: return "blue";
  }
};

/* ══════════════════════════════════════
   HOOKS
══════════════════════════════════════ */
