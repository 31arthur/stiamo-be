"""Every AI job, by name: the stories' jobs and the jobs of gauging a person. Each can be put on its own model and scored on its test set."""

from app.domain.aijobs import Job
from app.domain.insight_jobs import INSIGHT_JOBS
from app.domain.story_jobs import JOBS as STORY_JOBS

ALL_JOBS: dict[str, Job] = {**STORY_JOBS, **INSIGHT_JOBS}
