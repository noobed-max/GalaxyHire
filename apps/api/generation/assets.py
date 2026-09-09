"""Resolving rendered application assets to text.

Kept from JustHireMe's automation router when its browser auto-apply was removed. This part is not
auto-apply: it turns a stored asset path into the *text* a form needs, and the extension apply flow
(ARCHITECTURE.md §6) needs exactly that — the resume and cover letter it syncs are rendered
artifacts on disk, but what goes into a textarea is their text.
"""

from __future__ import annotations

from pathlib import Path


def resolve_cover_letter_text(asset_path: str, logger) -> str:
    """Resolve cover letter *text* from a stored asset path.

    The asset path may point at a ``.pdf`` or ``.md`` artifact. Form-fill needs the text, so this
    prefers the ``.md`` sibling and only reads text-like files — never the raw PDF bytes, and never
    the path string itself. Returning the path or PDF binary would put garbage into a real
    application form, so both are excluded deliberately rather than incidentally.

    Returns "" (with a warning) when no readable text file exists.
    """
    if not asset_path:
        return ""
    asset = Path(asset_path)
    md_path = asset.with_suffix(".md")
    for candidate in (md_path, asset):
        try:
            if candidate.suffix.lower() in {".md", ".txt"} and candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("could not read cover letter %s: %s", candidate, exc)
            return ""
    logger.warning(
        "no readable cover letter text for asset %s (.pdf exists=%s)",
        asset_path,
        asset.is_file() and asset.suffix.lower() == ".pdf",
    )
    return ""


