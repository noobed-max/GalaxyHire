// Shapes the extension exchanges with the GalaxyHire backend. The panel fetches a FillContext;
// the fill machinery consumes the Profile shape (mappers + the /fill/decide prose the backend
// builds from the same stored profile).

export interface Skill {
  id?: string
  name: string
  level?: string | null
  years?: number | null
  tags: string[]
}

export interface Project {
  id?: string
  title: string
  bullets: string[]
  skills: string[]
  role_tags: string[]
  verbatim?: boolean
}

export interface Profile {
  user_id: string
  identity: {
    name?: string | null
    email?: string | null
    phone?: string | null
    links: string[]
    citizenship?: string | null
    work_authorization?: string | null
    locations: string[]
    willing_to_relocate?: boolean | null
  }
  roles: string[]
  skills: Skill[]
  projects: Project[]
  experience: unknown[]
  education: unknown[]
  publications: unknown[]
  certifications: unknown[]
  preferences: {
    target_roles: string[]
    comp_min?: number | null
    comp_currency?: string | null
    remote_only?: boolean | null
    seniority_floor?: string | null
    seniority_ceiling?: string | null
    deal_breakers: string[]
  }
  anything_else: string
  profile_version: number
  updated_at?: string | null
}

// The per-job payload the backend prepares on Apply (docs/11). Identity + anything-else are fixed;
// skills/projects/experience are the job-tailored, field-sliced set.
export interface FillContext {
  job_id: string
  company: string
  title: string
  apply_url: string
  identity: {
    name?: string | null
    email?: string | null
    phone?: string | null
    links: string[]
    locations: string[]
    citizenship?: string | null
    work_authorization?: string | null
    willing_to_relocate?: boolean | null
  }
  anything_else: string
  skills: string[]
  projects: { title: string; bullets: string[]; skills: string[] }[]
  experience: { title?: string; company?: string; dates?: string; bullets?: string[] }[]
  resume_ref: string
  /** Backend-relative authenticated download path; works for corpus and manual jobs. */
  resume_url?: string
  resume_path: string
  ats_score: number
  prepared_at: string
}
