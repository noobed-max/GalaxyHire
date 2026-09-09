// Field scanning + value application. Ported from the user's autoapply extension (MIT, proven).
// The native-setter trick is essential: React/Vue ignore a plain `el.value = x`.

export type FieldKind = 'text' | 'textarea' | 'select' | 'checkbox' | 'checkbox-group' | 'radio-group'

export interface ScannedField {
  id: number
  kind: FieldKind
  label: string
  type: string
  required: boolean
  options: string[]
  elements: HTMLElement[]
}

const MAX_FIELDS = 80

const SKIPPED_INPUT_TYPES = new Set([
  'hidden',
  'submit',
  'button',
  'reset',
  'image',
  'file', // handled separately via DataTransfer (docs/06 §3)
  'password', // never type credentials into an unknown page
])

export function scanFields(root: ParentNode = document): ScannedField[] {
  const fields: ScannedField[] = []
  const seenGroups = new Set<string>()
  let id = 0

  const nodes = root.querySelectorAll<HTMLElement>('input, select, textarea')
  for (const node of nodes) {
    if (fields.length >= MAX_FIELDS) break
    if (!isFillable(node)) continue

    if (node instanceof HTMLSelectElement) {
      fields.push({
        id: id++,
        kind: 'select',
        label: labelFor(node),
        type: 'select',
        required: node.required,
        options: [...node.options].map((o) => o.text.trim()).filter(Boolean),
        elements: [node],
      })
      continue
    }
    if (node instanceof HTMLTextAreaElement) {
      fields.push({
        id: id++,
        kind: 'textarea',
        label: labelFor(node),
        type: 'textarea',
        required: node.required,
        options: [],
        elements: [node],
      })
      continue
    }
    if (!(node instanceof HTMLInputElement)) continue
    const type = node.type.toLowerCase()
    if (SKIPPED_INPUT_TYPES.has(type)) continue

    if (type === 'radio' || type === 'checkbox') {
      const groupKey = `${type}:${node.name}`
      if (node.name && seenGroups.has(groupKey)) continue
      const peers = node.name
        ? [
            ...root.querySelectorAll<HTMLInputElement>(
              `input[type="${type}"][name="${cssEscape(node.name)}"]`,
            ),
          ].filter(isFillable)
        : [node]
      if (node.name) seenGroups.add(groupKey)

      if (type === 'checkbox' && peers.length === 1) {
        fields.push({
          id: id++,
          kind: 'checkbox',
          label: labelFor(node),
          type: 'checkbox',
          required: node.required,
          options: ['true', 'false'],
          elements: [node],
        })
      } else {
        fields.push({
          id: id++,
          kind: type === 'radio' ? 'radio-group' : 'checkbox-group',
          label: groupLabel(peers),
          type,
          required: peers.some((p) => p.required),
          options: peers.map(optionLabel),
          elements: peers,
        })
      }
      continue
    }

    fields.push({
      id: id++,
      kind: 'text',
      label: labelFor(node),
      type,
      required: node.required,
      options: [],
      elements: [node],
    })
  }
  return fields.filter((f) => f.label)
}

export function isFillable(el: HTMLElement): boolean {
  const input = el as HTMLInputElement
  if (input.disabled || input.readOnly) return false
  if (el.getAttribute('aria-hidden') === 'true') return false
  const style = getComputedStyle(el)
  if (style.display === 'none' || style.visibility === 'hidden') return false
  const rect = el.getBoundingClientRect()
  if (rect.width === 0 && rect.height === 0) {
    const type = input.type?.toLowerCase()
    if (type !== 'radio' && type !== 'checkbox') return false
    if (!el.offsetParent) return false
  }
  return true
}

export function labelFor(el: HTMLElement): string {
  const aria = el.getAttribute('aria-label')?.trim()
  if (aria) return aria
  const labelledBy = el.getAttribute('aria-labelledby')
  if (labelledBy) {
    const text = labelledBy
      .split(/\s+/)
      .map((refId) => document.getElementById(refId)?.textContent?.trim() ?? '')
      .filter(Boolean)
      .join(' ')
    if (text) return clean(text)
  }
  if (el.id) {
    const explicit = document.querySelector<HTMLLabelElement>(`label[for="${cssEscape(el.id)}"]`)
    if (explicit?.textContent?.trim()) return clean(explicit.textContent)
  }
  const wrapping = el.closest('label')
  if (wrapping?.textContent?.trim()) return clean(wrapping.textContent)
  const placeholder = el.getAttribute('placeholder')?.trim()
  if (placeholder) return placeholder
  const title = el.getAttribute('title')?.trim()
  if (title) return title
  const name = el.getAttribute('name')?.trim()
  if (name) return humanize(name)
  return ''
}

function groupLabel(peers: HTMLElement[]): string {
  const first = peers[0]
  const fieldset = first.closest('fieldset')
  const legend = fieldset?.querySelector('legend')?.textContent?.trim()
  const container = first.closest('.field, .form-group, div')
  const containerLabel = container?.querySelector('label')?.textContent?.trim()
  const name = first.getAttribute('name')?.trim()
  const ownLabel = optionLabel(first as HTMLInputElement)
  for (const candidate of [containerLabel, legend, name ? humanize(name) : '']) {
    const text = candidate ? clean(candidate) : ''
    if (text && text !== ownLabel) return text
  }
  return name ? humanize(name) : ownLabel
}

function optionLabel(input: HTMLInputElement): string {
  return labelFor(input) || input.value || ''
}

// --- applying ---------------------------------------------------------------

export function applyValue(field: ScannedField, raw: string): boolean {
  switch (field.kind) {
    case 'text':
    case 'textarea':
      setText(field.elements[0] as HTMLInputElement | HTMLTextAreaElement, raw)
      return true
    case 'select':
      return setSelect(field.elements[0] as HTMLSelectElement, raw)
    case 'checkbox': {
      const wanted = /^(true|yes|1|on|checked)$/i.test(raw.trim())
      setChecked(field.elements[0] as HTMLInputElement, wanted)
      return true
    }
    case 'radio-group': {
      const match = bestOption(field.options, raw)
      if (match === -1) return false
      setChecked(field.elements[match] as HTMLInputElement, true)
      return true
    }
    case 'checkbox-group': {
      const wanted = raw.split(',').map((s) => s.trim()).filter(Boolean)
      let any = false
      for (const want of wanted) {
        const match = bestOption(field.options, want)
        if (match !== -1) {
          setChecked(field.elements[match] as HTMLInputElement, true)
          any = true
        }
      }
      return any
    }
    default:
      return false
  }
}

/** Native prototype setter + input/change + focus/blur so React/Vue observe the change. */
export function setText(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto =
    el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
  el.focus()
  if (setter) setter.call(el, value)
  else el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
  el.blur()
}

export function setChecked(el: HTMLInputElement, checked: boolean) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked')?.set
  el.focus()
  if (setter) setter.call(el, checked)
  else el.checked = checked
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
  el.blur()
}

export function setSelect(el: HTMLSelectElement, value: string): boolean {
  const texts = [...el.options].map((o) => o.text.trim())
  const index = bestOption(texts, value)
  if (index === -1) return false
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set
  el.focus()
  if (setter) setter.call(el, el.options[index].value)
  else el.value = el.options[index].value
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
  el.blur()
  return true
}

/** Exact, then case-insensitive, then substring either direction. */
export function bestOption(options: string[], value: string): number {
  const want = value.trim()
  if (!want) return -1
  const exact = options.findIndex((o) => o === want)
  if (exact !== -1) return exact
  const lower = want.toLowerCase()
  const ci = options.findIndex((o) => o.trim().toLowerCase() === lower)
  if (ci !== -1) return ci
  return options.findIndex((o) => {
    const opt = o.trim().toLowerCase()
    return opt.length > 0 && (opt.includes(lower) || lower.includes(opt))
  })
}

// --- helpers ----------------------------------------------------------------

function clean(text: string): string {
  return text.replace(/\s+/g, ' ').replace(/\s*\*\s*$/, '').trim()
}
function humanize(name: string): string {
  return clean(name.replace(/[_\-.[\]]+/g, ' ')).replace(/\b\w/g, (c) => c.toUpperCase())
}
export function cssEscape(value: string): string {
  return typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(value) : value.replace(/"/g, '\\"')
}
