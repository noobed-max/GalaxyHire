"""Recalibrate QPP with query styles real users actually type.

The first calibration used job titles as the "good" population. Titles are fluent noun phrases that
embed close to job documents; real queries are often keyword bags ("devops terraform aws"), whose
blended vector sits further from any single job. That mismatch made good keyword queries score "weak".
"""
import asyncio
import statistics
from sqlalchemy import text
from galaxy.db.engine import get_sessionmaker
from galaxy.search.embedder import get_embedder, to_pgvector

# Keyword-style queries a user would type, all of which SHOULD return relevant jobs.
KEYWORD_GOOD = [
    "kubernetes platform engineer", "react frontend developer", "devops terraform aws",
    "python backend api", "machine learning engineer", "data engineer sql",
    "golang microservices", "site reliability engineer", "typescript node developer",
    "security engineer cloud", "android kotlin developer", "sales representative",
    "customer success manager", "product designer figma", "java spring boot",
]
BAD = [
    "underwater basket weaving zookeeper", "asdfghjkl qwerty", "purple monkey dishwasher",
    "zxcvbnm poiuyt lkjhg", "flibbertigibbet wombat trombone",
    "recipe for sourdough bread", "how to change a bicycle tyre",
    "symptoms of vitamin d deficiency", "best hiking trails in patagonia",
    "quantum bagpipe taxidermy",
]

async def mean10(s, emb, q):
    vec = to_pgvector(emb.embed([q])[0])
    rows = (await s.execute(text(
        "SELECT 1 - (embedding <=> CAST(:v AS vector)) FROM canonical_jobs "
        "WHERE embedding IS NOT NULL AND status='open' ORDER BY embedding <=> CAST(:v AS vector) LIMIT 10"),
        {"v": vec})).all()
    return statistics.mean(float(r[0]) for r in rows) if rows else None

async def main():
    emb, sm = get_embedder(), get_sessionmaker()
    async with sm() as s:
        titles = [r[0] for r in (await s.execute(text(
            "SELECT title FROM (SELECT DISTINCT title FROM canonical_jobs "
            "WHERE length(title) BETWEEN 12 AND 60) t ORDER BY random() LIMIT 30"))).all()]
        pops = {"title-style (good)": titles, "keyword-style (good)": KEYWORD_GOOD, "bad": BAD}
        got = {}
        for name, qs in pops.items():
            vals = sorted(v for v in [await mean10(s, emb, q) for q in qs] if v is not None)
            got[name] = vals
            print(f"{name:22} n={len(vals):3} min={vals[0]:.3f} p10={vals[len(vals)//10]:.3f} "
                  f"median={statistics.median(vals):.3f} max={vals[-1]:.3f}")
        good_all = sorted(got["title-style (good)"] + got["keyword-style (good)"])
        bad = got["bad"]
        print(f"\nall-good min = {good_all[0]:.3f}   bad max = {bad[-1]:.3f}   "
              f"clean gap = {'YES' if good_all[0] > bad[-1] else 'NO (overlap)'}")
        print("per-query keyword scores (lowest first):")
        for q, v in sorted(zip(KEYWORD_GOOD, [await mean10(s, emb, q) for q in KEYWORD_GOOD]), key=lambda t: t[1]):
            print(f"   {v:.3f}  {q}")
asyncio.run(main())
