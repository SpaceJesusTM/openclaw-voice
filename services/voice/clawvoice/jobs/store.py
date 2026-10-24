"""Persistent, bounded job records (one private JSON file per agent).

Simple and recoverable: every state change rewrites the agent's file
atomically (0600, ignored .local/). On restart, unfinished jobs are recovered
or marked uncertain by the job manager; finished outcomes stay retrievable so
a disconnected user can still ask "did that finish?". Records keep bounded
summaries, not raw transcripts.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from clawvoice.jobs.models import Job
from clawvoice.paths import private_dir, write_private

MAX_JOBS = 200
RETENTION = timedelta(days=7)


class JobStore:
    def __init__(self, directory: Path, agent_id: str, *, persist: bool = True):
        self.agent_id = agent_id
        self.persist = persist
        self.path = directory / f"jobs-{agent_id}.json"
        self.jobs: dict[str, Job] = {}
        if persist:
            private_dir(directory)
            self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
            for record in records.get("jobs", []):
                job = Job.from_dict(record)
                if job.agent_id == self.agent_id:
                    self.jobs[job.id] = job
        except (OSError, ValueError, TypeError) as error:
            backup = self.path.with_suffix(".corrupt.json")
            logger.error(f"Job store for {self.agent_id} unreadable ({type(error).__name__}); moved to {backup.name}")
            self.path.replace(backup)

    def save(self) -> None:
        self._prune()
        if not self.persist:
            return
        payload = {"version": 1, "agent_id": self.agent_id, "jobs": [job.to_dict() for job in self.ordered()]}
        write_private(self.path, json.dumps(payload, indent=1))

    def _prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - RETENTION
        ordered = self.ordered()
        keep = []
        for job in ordered:
            if job.terminal and datetime.fromisoformat(job.updated_at) < cutoff:
                continue
            keep.append(job)
        if len(keep) > MAX_JOBS:
            active = [job for job in keep if not job.terminal]
            finished = [job for job in keep if job.terminal][: max(0, MAX_JOBS - len(active))]
            keep = active + finished
        self.jobs = {job.id: job for job in keep}

    def ordered(self) -> list[Job]:
        """Newest first."""
        return sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)

    def put(self, job: Job) -> None:
        job.touch()
        self.jobs[job.id] = job
        self.save()

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)
