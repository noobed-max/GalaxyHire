import { beforeAll } from 'vitest'

// jsdom reports every element as 0×0, which our visibility check (isFillable) treats as hidden.
// Give elements a non-zero box so field scanning behaves like a rendered page.
beforeAll(() => {
  Element.prototype.getBoundingClientRect = function (): DOMRect {
    return {
      width: 120,
      height: 24,
      top: 0,
      left: 0,
      right: 120,
      bottom: 24,
      x: 0,
      y: 0,
      toJSON() {},
    } as DOMRect
  }
})
