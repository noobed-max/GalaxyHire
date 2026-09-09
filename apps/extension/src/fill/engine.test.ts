import { beforeEach, describe, expect, it, vi } from 'vitest'
import { applyValue, bestOption, scanFields, setText } from './engine'

function setBody(html: string) {
  document.body.innerHTML = html
}

describe('bestOption', () => {
  const opts = ['United States', 'United Kingdom', 'Canada']
  it('matches exact, case-insensitive, and substring', () => {
    expect(bestOption(opts, 'Canada')).toBe(2)
    expect(bestOption(opts, 'canada')).toBe(2)
    expect(bestOption(opts, 'United King')).toBe(1)
    expect(bestOption(opts, 'nowhere')).toBe(-1)
  })
})

describe('scanFields', () => {
  it('picks up labelled text, select, checkbox, and radio group', () => {
    setBody(`
      <label for="fn">First name</label><input id="fn" type="text" />
      <label for="country">Country</label>
      <select id="country"><option>United States</option><option>Canada</option></select>
      <label><input type="checkbox" name="agree" /> I agree</label>
      <fieldset><legend>Gender</legend>
        <label><input type="radio" name="g" value="f" /> Female</label>
        <label><input type="radio" name="g" value="m" /> Male</label>
      </fieldset>
    `)
    const fields = scanFields()
    const kinds = fields.map((f) => f.kind)
    expect(kinds).toContain('text')
    expect(kinds).toContain('select')
    expect(kinds).toContain('checkbox')
    expect(kinds).toContain('radio-group')
    const radio = fields.find((f) => f.kind === 'radio-group')!
    expect(radio.options).toEqual(['Female', 'Male'])
  })

  it('skips file and password inputs', () => {
    setBody(`
      <label for="r">Resume</label><input id="r" type="file" />
      <label for="p">Password</label><input id="p" type="password" />
      <label for="e">Email</label><input id="e" type="email" />
    `)
    const labels = scanFields().map((f) => f.label)
    expect(labels).toEqual(['Email'])
  })
})

describe('applyValue via native setters', () => {
  beforeEach(() => setBody(''))

  it('sets text and fires input + change (React/Vue observability)', () => {
    setBody('<label for="fn">First name</label><input id="fn" type="text" />')
    const field = scanFields()[0]
    const input = field.elements[0] as HTMLInputElement
    const onInput = vi.fn()
    const onChange = vi.fn()
    input.addEventListener('input', onInput)
    input.addEventListener('change', onChange)

    expect(applyValue(field, 'Ada')).toBe(true)
    expect(input.value).toBe('Ada')
    expect(onInput).toHaveBeenCalledOnce()
    expect(onChange).toHaveBeenCalledOnce()
  })

  it('selects the matching dropdown option', () => {
    setBody(`<label for="c">Country</label>
      <select id="c"><option>United States</option><option>Canada</option></select>`)
    const field = scanFields()[0]
    expect(applyValue(field, 'canada')).toBe(true)
    expect((field.elements[0] as HTMLSelectElement).value).toBe('Canada')
  })

  it('checks a single checkbox for a truthy value', () => {
    setBody('<label><input type="checkbox" name="agree" /> I agree</label>')
    const field = scanFields()[0]
    applyValue(field, 'true')
    expect((field.elements[0] as HTMLInputElement).checked).toBe(true)
  })

  it('native setter path uses the prototype descriptor', () => {
    setBody('<input id="x" type="text" />')
    const el = document.getElementById('x') as HTMLInputElement
    setText(el, 'hello')
    expect(el.value).toBe('hello')
  })
})
