import { describe, expect, it, vi } from 'vitest'
import { installSubmitGuard, isSubmitLike } from './guard'

describe('isSubmitLike', () => {
  it('flags submit inputs and submit-worded controls', () => {
    const submitInput = document.createElement('input')
    submitInput.type = 'submit'
    expect(isSubmitLike(submitInput)).toBe(true)

    const applyBtn = document.createElement('button')
    applyBtn.textContent = 'Apply now'
    expect(isSubmitLike(applyBtn)).toBe(true)

    const nextBtn = document.createElement('button')
    nextBtn.textContent = 'Continue'
    expect(isSubmitLike(nextBtn)).toBe(true)

    // an explicit type="button" with no submit wording is safe to click
    const plain = document.createElement('button')
    plain.type = 'button'
    plain.textContent = 'Add another'
    expect(isSubmitLike(plain)).toBe(false)

    // NOTE: a <button> with no type defaults to type=submit per HTML spec, so the guard
    // conservatively treats it as submit-like — verify that intended behavior:
    const defaultBtn = document.createElement('button')
    defaultBtn.textContent = 'Add another'
    expect(isSubmitLike(defaultBtn)).toBe(true)
  })
})

describe('installSubmitGuard', () => {
  it('blocks an untrusted (script-generated) click on a submit control', () => {
    document.body.innerHTML = '<button id="submit">Submit application</button>'
    const status = vi.fn()
    const release = installSubmitGuard(status)

    const btn = document.getElementById('submit') as HTMLButtonElement
    const onClick = vi.fn()
    btn.addEventListener('click', onClick)

    // a synthetic click is untrusted (isTrusted === false), like a script-driven agent click
    btn.click()

    expect(onClick).not.toHaveBeenCalled() // guard stopped propagation before the handler
    expect(status).toHaveBeenCalledOnce()
    release()
  })

  it('lets clicks on non-submit controls through', () => {
    document.body.innerHTML = '<button id="add" type="button">Add another</button>'
    const release = installSubmitGuard(vi.fn())
    const btn = document.getElementById('add') as HTMLButtonElement
    const onClick = vi.fn()
    btn.addEventListener('click', onClick)
    btn.click()
    expect(onClick).toHaveBeenCalledOnce()
    release()
  })
})
