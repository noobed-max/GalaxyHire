"""Per-field merge resolver (docs/03 §3).

Given N observations of the same canonical job, pick the winning value PER FIELD — so the
salary can come from Greenhouse, the description from LinkedIn, and the apply URL from the ATS,
all in one record. Pure and side-effect-free (runs in the dedup hot loop).
"""

from __future__ import annotations

from galaxy.models.enums import SalarySource, source_trust
from galaxy.models.job import (
    Compensation,
    FieldWithProvenance,
    Location,
    SourceObservation,
)


def _by_trust_then_recency(observations: list[SourceObservation]) -> list[SourceObservation]:
    return sorted(
        observations,
        key=lambda o: (source_trust(o.site), o.observed_at),
        reverse=True,
    )


def _prov(obs: SourceObservation, value) -> FieldWithProvenance:
    return FieldWithProvenance(
        value=value, source=obs.site, source_id=obs.source_job_id, observed_at=obs.observed_at
    )


def _pick_description(obs: list[SourceObservation]) -> tuple[str | None, SourceObservation]:
    """Longest / richest description (ATS feeds usually beat board snippets)."""
    best = max(
        obs,
        key=lambda o: len(o.fields.description_md or o.fields.description_html or ""),
    )
    return best.fields.description_md or best.fields.description_html, best


def _pick_url(obs: list[SourceObservation]) -> tuple[str, SourceObservation]:
    """Prefer the direct ATS apply URL over a board redirect (better for autofill)."""
    ats_first = sorted(obs, key=lambda o: source_trust(o.site), reverse=True)
    return ats_first[0].url, ats_first[0]


def _pick_compensation(obs: list[SourceObservation]) -> tuple[Compensation | None, SourceObservation]:
    """Prefer salary_source=direct over parsed; widest credible range among those."""
    with_comp = [o for o in obs if o.fields.compensation]
    if not with_comp:
        return None, obs[0]
    direct = [o for o in with_comp if o.fields.compensation.salary_source == SalarySource.DIRECT]
    pool = direct or with_comp

    def spread(o: SourceObservation) -> float:
        c = o.fields.compensation
        lo, hi = c.min_amount or 0, c.max_amount or 0
        return hi - lo

    best = max(pool, key=spread)
    return best.fields.compensation, best


def _pick_date_posted(obs: list[SourceObservation]):
    """Earliest credible date (true post date, not re-index date)."""
    dated = [o for o in obs if o.fields.date_posted]
    if not dated:
        return None, obs[0]
    best = min(dated, key=lambda o: o.fields.date_posted)
    return best.fields.date_posted, best


class MergeResolver:
    """Default field-policy resolver (docs/03 §3). Swap via DI for alternate policies."""

    def merge(self, observations: list[SourceObservation]) -> dict[str, FieldWithProvenance]:
        assert observations, "merge requires at least one observation"
        ranked = _by_trust_then_recency(observations)
        top = ranked[0]  # highest-trust, most-recent — wins title/company ties

        fields: dict[str, FieldWithProvenance] = {}

        # title / company — highest-trust source, ties by recency
        fields["title"] = _prov(top, top.fields.title)
        fields["company"] = _prov(top, top.fields.company)

        # location — take the most specific (has city) among trusted sources, else top
        loc_obs = next(
            (o for o in ranked if o.fields.location.city),
            top,
        )
        fields["location"] = _prov(loc_obs, loc_obs.fields.location)

        desc, desc_obs = _pick_description(observations)
        fields["description"] = _prov(desc_obs, desc)

        url, url_obs = _pick_url(observations)
        fields["url"] = _prov(url_obs, url)

        comp, comp_obs = _pick_compensation(observations)
        if comp is not None:
            fields["compensation"] = _prov(comp_obs, comp)

        dt, dt_obs = _pick_date_posted(observations)
        if dt is not None:
            fields["date_posted"] = _prov(dt_obs, dt)

        # derived fields — take from the highest-trust observation that has them
        for name, getter in (
            ("seniority", lambda f: f.seniority),
            ("min_years_experience", lambda f: f.min_years_experience),
            ("onsite_policy", lambda f: f.onsite_policy),
            ("clearance_required", lambda f: f.clearance_required),
        ):
            src = next((o for o in ranked if getter(o.fields) is not None), None)
            if src is not None:
                fields[name] = _prov(src, getter(src.fields))

        # jd_keywords — union across sources, preserving frequency-ish order from the top source
        seen: dict[str, None] = {}
        for o in ranked:
            for kw in o.fields.jd_keywords:
                seen.setdefault(kw, None)
        if seen:
            fields["jd_keywords"] = _prov(top, list(seen.keys()))

        # jd_skills — union across sources (curated vocab, so a plain set-union is safe)
        skills: dict[str, None] = {}
        for o in ranked:
            for sk in o.fields.jd_skills:
                skills.setdefault(sk, None)
        if skills:
            fields["jd_skills"] = _prov(top, sorted(skills))

        return fields


def location_value(fields: dict[str, FieldWithProvenance]) -> Location:
    fw = fields.get("location")
    return fw.value if fw else Location()


