"""Scores a model on the AI jobs' test sets, so a job is moved onto a model (a cheaper one, a newer one) only once it does the job.

    python -m app.scripts.eval_jobs --provider openrouter --model mistralai/mistral-small --key "$OPENROUTER_API_KEY"
    python -m app.scripts.eval_jobs --provider gemini --model gemini-2.0-flash --key "$GEMINI_API_KEY" --job margin_notes -v

Reads `evals/<job>.jsonl` (one case per line: {"name", "input", "expect"}), runs each case the way the app does (same prompt, examples,
checks and retry), and prints the agreement per job. A job passes at 85%. Exits 1 if any job asked for fails, so it can gate a deploy.
Grow the sets from real chapters: when you correct a note before approving it, that chapter with your corrected notes is a new case.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from app.application.aijobs import JobRunner
from app.domain.job_evals import PASS_MARK, SCORERS, SetScore
from app.domain.jobs import ALL_JOBS as JOBS
from app.infrastructure.llm.gemini import GeminiClient
from app.infrastructure.llm.openai_compat import OpenAiCompatibleClient

#: The test sets: ./evals where it is run (the server image keeps them in /srv/evals), else next to the source.
EVALS = next((p for p in (Path.cwd() / "evals", Path(__file__).resolve().parents[2] / "evals") if p.is_dir()), Path("evals"))


def load(job: str) -> list[dict]:
    """A job's cases: its own file, or (for the six lens coders) the shared lens_coding.jsonl, whose cases name their job."""
    path = EVALS / f"{job}.jsonl"
    if not path.exists() and job.startswith("lens_") and (EVALS / "lens_coding.jsonl").exists():
        return [c for c in _read(EVALS / "lens_coding.jsonl") if c.get("job") == job]
    return _read(path) if path.exists() else []


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("//")]


async def run(provider: str, model: str, key: str, jobs: list[str], verbose: bool) -> list[SetScore]:
    async with httpx.AsyncClient(timeout=120) as http:
        client = GeminiClient(key, http) if provider == "gemini" else OpenAiCompatibleClient(provider, key, http)
        runner = JobRunner(client, model)
        scores = []
        for name in jobs:
            job, scorer = JOBS[name], SCORERS[name]
            result = SetScore(name, model, [])
            for case in load(name):
                done = await runner.run(job, case["input"])
                result.fell_back += 0 if done.ok else 1
                s = scorer(case["name"], done.output, case["expect"])
                result.cases.append(s)
                if verbose:
                    mark = "ok " if s.got == s.points else "MISS"
                    print(f"  {mark} {name}/{s.name}: {s.got}/{s.points}" + (f"  ({'; '.join(s.misses)})" if s.misses else "") + ("" if done.ok else "  [fell back]"))
            scores.append(result)
        return scores


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", required=True, choices=["openai", "gemini", "openrouter"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--job", action="append", choices=sorted(JOBS), help="Only this job (repeatable). Default: all.")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    scores = asyncio.run(run(a.provider, a.model, a.key, a.job or sorted(JOBS), a.verbose))
    for s in scores:
        verdict = "PASS" if s.passed else "FAIL"
        print(f"{verdict} {s.job} on {s.model}: {s.agreement:.0%} agreement over {len(s.cases)} cases (needs {PASS_MARK:.0%}); fell back {s.fell_back}×")
    sys.exit(0 if all(s.passed for s in scores) else 1)


if __name__ == "__main__":
    main()
