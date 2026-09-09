// Deterministic per-platform field fill (docs/06 §3) — no LLM. Recognizes common identity
// fields by autocomplete / label / name and fills them from the profile. Covers the reliable
// core of Greenhouse, Lever, Ashby, Workday, Workable; the LLM Fast pass handles the rest.

import type { Profile } from '../shared/types'
import { profileField } from '../shared/fillProfile'
import { applyValue, type ScannedField } from './engine'

export type Platform =
  | 'greenhouse'
  | 'lever'
  | 'ashby'
  | 'workday'
  | 'workable'
  | 'unknown'

export function detectPlatform(): Platform {
  const host = location.hostname
  const html = document.documentElement.innerHTML
  if (/greenhouse|boards\.greenhouse/i.test(host) || /greenhouse\.io/i.test(html)) return 'greenhouse'
  if (/lever\.co/i.test(host)) return 'lever'
  if (/ashbyhq\.com/i.test(host)) return 'ashby'
  if (/myworkdayjobs\.com|workday/i.test(host)) return 'workday'
  if (/workable\.com/i.test(host)) return 'workable'
  return 'unknown'
}

// profile-key → matchers against a field's autocomplete token or its label/name text.
interface Rule {
  key: string
  autocomplete?: string[]
  label: RegExp
}

const RULES: Rule[] = [
  { key: 'firstName', autocomplete: ['given-name'], label: /\bfirst\s*name\b|\bgiven\s*name\b/i },
  { key: 'lastName', autocomplete: ['family-name'], label: /\blast\s*name\b|\bfamily\s*name\b|\bsurname\b/i },
  { key: 'name', autocomplete: ['name'], label: /\bfull\s*name\b|^name$/i },
  { key: 'email', autocomplete: ['email'], label: /\be-?mail\b/i },
  { key: 'phone', autocomplete: ['tel'], label: /\bphone\b|\bmobile\b|\btelephone\b/i },
  { key: 'location', autocomplete: ['address-level2'], label: /\blocation\b|\bcity\b|\bcurrent\s*location\b/i },
  { key: 'linkedin', label: /\blinkedin\b/i },
  { key: 'github', label: /\bgithub\b/i },
  { key: 'website', autocomplete: ['url'], label: /\bwebsite\b|\bportfolio\b|\bpersonal\s*site\b/i },
]

function autocompleteToken(field: ScannedField): string {
  const el = field.elements[0]
  return (el.getAttribute('autocomplete') || '').toLowerCase()
}

function matchKey(field: ScannedField): string | undefined {
  const ac = autocompleteToken(field)
  const label = field.label
  for (const rule of RULES) {
    if (rule.autocomplete?.some((t) => ac.includes(t))) return rule.key
    if (rule.label.test(label)) return rule.key
  }
  return undefined
}

export interface MapperResult {
  filled: number
  lines: string[]
  remaining: ScannedField[]
}

/** Fill every field a deterministic rule recognizes; return the ones left for the LLM pass. */
export function runDeterministicFill(fields: ScannedField[], profile: Profile): MapperResult {
  const lines: string[] = []
  const remaining: ScannedField[] = []
  let filled = 0

  for (const field of fields) {
    if (field.kind !== 'text') {
      remaining.push(field)
      continue
    }
    const key = matchKey(field)
    const value = key ? profileField(profile, key) : undefined
    if (key && value) {
      if (applyValue(field, value)) {
        filled++
        lines.push(`✓ ${field.label}: ${value}`)
        continue
      }
    }
    remaining.push(field)
  }
  return { filled, lines, remaining }
}
