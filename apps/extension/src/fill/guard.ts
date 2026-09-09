// Never auto-submit (docs/06 §3). A prompt telling the model not to click Submit is not
// enforcement — this capture-phase guard drops every UNTRUSTED (script-generated) click on a
// submit-like control and every untrusted form submission during a run. Real user clicks carry
// isTrusted === true and pass straight through.

const SUBMIT_WORDS =
  /\b(submit|apply|next|continue|save|send|finish|proceed|confirm|review\s+and\s+submit)\b/i

export function installSubmitGuard(status: (text: string) => void): () => void {
  const onClick = (event: MouseEvent) => {
    if (event.isTrusted) return
    const target = event.target
    if (!(target instanceof Element)) return
    const control = target.closest<HTMLElement>(
      'button, input[type="submit"], input[type="button"], input[type="image"], a[role="button"], [role="button"]',
    )
    if (!control || !isSubmitLike(control)) return
    event.preventDefault()
    event.stopImmediatePropagation()
    status(`Blocked an automated click on "${truncate(controlLabel(control), 40)}" — submit is yours.`)
  }

  const onSubmit = (event: Event) => {
    if (event.isTrusted) return
    event.preventDefault()
    event.stopImmediatePropagation()
    status('Blocked an automated form submission.')
  }

  document.addEventListener('click', onClick, true)
  document.addEventListener('submit', onSubmit, true)
  return () => {
    document.removeEventListener('click', onClick, true)
    document.removeEventListener('submit', onSubmit, true)
  }
}

export function isSubmitLike(el: HTMLElement): boolean {
  if (el instanceof HTMLInputElement && el.type.toLowerCase() === 'submit') return true
  if (el instanceof HTMLButtonElement && el.type.toLowerCase() === 'submit') return true
  return SUBMIT_WORDS.test(controlLabel(el))
}

function controlLabel(el: HTMLElement): string {
  const value = el instanceof HTMLInputElement ? el.value : ''
  return [el.getAttribute('aria-label'), el.getAttribute('title'), value, el.textContent]
    .filter(Boolean)
    .join(' ')
    .trim()
}

function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max)}…` : value
}
