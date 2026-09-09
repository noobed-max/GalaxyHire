"""Lead text analysis — extraction and quality heuristics over job posting text.

These modules lived in JustHireMe's `discovery/` package, which was removed with its scraper. They
are not scraping code: `lead_intel` is pure text extraction (budget, company, location, tech
stack, urgency, signal quality) depending only on `core.*`, and `quality_gate` scores whether a
posting looks like a genuine professional role. Both are still used by the MCP server and are
generally useful over corpus-sourced jobs, so they were kept and rehoused rather than deleted with
the scraper around them.
"""
