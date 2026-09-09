// Profile plumbing for the deterministic mappers.

import type { FillContext, Profile } from './types'

/** Adapt a per-job fill context (docs/11) into the Profile shape the fill machinery consumes.
 *  Identity + anything-else are the fixed facts; skills/projects/experience are job-tailored. */
export function fillContextToProfile(ctx: FillContext): Profile {
  return {
    user_id: '',
    identity: {
      ...ctx.identity,
      links: ctx.identity.links ?? [],
      locations: ctx.identity.locations ?? [],
    },
    roles: [],
    skills: (ctx.skills ?? []).map((name) => ({ name, tags: [] })),
    projects: (ctx.projects ?? []).map((p) => ({
      title: p.title,
      bullets: p.bullets ?? [],
      skills: p.skills ?? [],
      role_tags: [],
    })),
    experience: ctx.experience ?? [],
    education: [],
    publications: [],
    certifications: [],
    preferences: { target_roles: [], deal_breakers: [] },
    anything_else: ctx.anything_else ?? '',
    profile_version: 1,
  }
}

/** Flat field lookups for the deterministic ATS mappers (no LLM). */
export function profileField(p: Profile, key: string): string | undefined {
  const id = p.identity
  const [first, ...rest] = (id.name ?? '').trim().split(/\s+/)
  const last = rest.join(' ')
  const map: Record<string, string | undefined> = {
    name: id.name ?? undefined,
    firstName: first || undefined,
    lastName: last || undefined,
    email: id.email ?? undefined,
    phone: id.phone ?? undefined,
    location: id.locations?.[0],
    linkedin: id.links?.find((l) => /linkedin\.com/i.test(l)),
    github: id.links?.find((l) => /github\.com/i.test(l)),
    website: id.links?.[0],
  }
  return map[key]
}
