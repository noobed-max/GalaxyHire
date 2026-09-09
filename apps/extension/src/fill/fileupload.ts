// Assisted resume attach (docs/06 §3). Browsers DO allow setting a file input via DataTransfer
// from a content script — this is a product choice, not a hard limit. We attach the tailored
// resume to the most likely resume/CV file input; custom drag-drop widgets (no real <input
// type=file>) fall back to manual upload.

const RESUME_HINT = /\b(resume|cv|curriculum)\b/i

/** Find the file input most likely to be the resume/CV upload. */
export function findResumeInput(root: ParentNode = document): HTMLInputElement | null {
  const inputs = [...root.querySelectorAll<HTMLInputElement>('input[type="file"]')].filter(
    (el) => !el.disabled,
  )
  if (inputs.length === 0) return null
  const labelled = inputs.find((el) => RESUME_HINT.test(fileInputContext(el)))
  return labelled ?? inputs[0]
}

function fileInputContext(el: HTMLInputElement): string {
  const bits = [
    el.getAttribute('name'),
    el.getAttribute('id'),
    el.getAttribute('aria-label'),
    el.closest('label')?.textContent,
    el.id ? document.querySelector(`label[for="${el.id}"]`)?.textContent : '',
  ]
  return bits.filter(Boolean).join(' ')
}

/** Attach `file` to `input` via DataTransfer and fire change. Returns false if the browser
 *  rejects the assignment (some sandboxed/custom widgets). */
export function attachFile(input: HTMLInputElement, file: File): boolean {
  try {
    const dt = new DataTransfer()
    dt.items.add(file)
    input.files = dt.files
    if (input.files.length !== 1) return false
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  } catch {
    return false
  }
}

export function blobToFile(blob: Blob, filename: string): File {
  return new File([blob], filename, { type: blob.type || 'application/pdf' })
}
