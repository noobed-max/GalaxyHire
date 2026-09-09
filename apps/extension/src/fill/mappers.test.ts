import { describe, expect, it } from 'vitest'
import { scanFields } from './engine'
import { runDeterministicFill } from './mappers'
import type { Profile } from '../shared/types'

const PROFILE: Profile = {
  user_id: 'u',
  identity: {
    name: 'Ada Lovelace',
    email: 'ada@example.com',
    phone: '555-0100',
    links: ['https://github.com/ada', 'https://linkedin.com/in/ada'],
    locations: ['London, UK'],
  },
  roles: [],
  skills: [],
  projects: [],
  experience: [],
  education: [],
  publications: [],
  certifications: [],
  preferences: { target_roles: [], deal_breakers: [] },
  anything_else: '',
  profile_version: 1,
}

describe('runDeterministicFill', () => {
  it('fills identity fields by label / autocomplete without an LLM', () => {
    document.body.innerHTML = `
      <label for="fn">First Name</label><input id="fn" type="text" autocomplete="given-name" />
      <label for="ln">Last Name</label><input id="ln" type="text" autocomplete="family-name" />
      <label for="em">Email</label><input id="em" type="email" />
      <label for="ph">Phone</label><input id="ph" type="tel" />
      <label for="li">LinkedIn</label><input id="li" type="url" />
      <label for="q">Why do you want this job?</label><textarea id="q"></textarea>
    `
    const fields = scanFields()
    const res = runDeterministicFill(fields, PROFILE)

    expect((document.getElementById('fn') as HTMLInputElement).value).toBe('Ada')
    expect((document.getElementById('ln') as HTMLInputElement).value).toBe('Lovelace')
    expect((document.getElementById('em') as HTMLInputElement).value).toBe('ada@example.com')
    expect((document.getElementById('ph') as HTMLInputElement).value).toBe('555-0100')
    expect((document.getElementById('li') as HTMLInputElement).value).toContain('linkedin.com/in/ada')
    expect(res.filled).toBe(5)
    // the free-text question is left for the LLM pass
    expect(res.remaining.map((f) => f.label)).toContain('Why do you want this job?')
  })
})

