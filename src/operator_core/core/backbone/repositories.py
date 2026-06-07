from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from .models import Event, Job, Run


class JobRepository(Protocol):
    def create(self, job: Job) -> Job: ...

    def get(self, job_id: str) -> Job | None: ...

    def update(self, job: Job) -> Job: ...

    def list_by_project(self, project_key: str) -> list[Job]: ...


class RunRepository(Protocol):
    def create(self, run: Run) -> Run: ...

    def get(self, run_id: str) -> Run | None: ...

    def update(self, run: Run) -> Run: ...

    def list_by_job(self, job_id: str) -> list[Run]: ...


class EventRepository(Protocol):
    def append(self, event: Event) -> Event: ...

    def list_for_entity(self, project_key: str, entity_type: str, entity_id: str) -> list[Event]: ...


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._items: dict[str, Job] = {}

    def create(self, job: Job) -> Job:
        self._items[job.job_id] = deepcopy(job)
        return deepcopy(job)

    def get(self, job_id: str) -> Job | None:
        job = self._items.get(job_id)
        return deepcopy(job) if job else None

    def update(self, job: Job) -> Job:
        self._items[job.job_id] = deepcopy(job)
        return deepcopy(job)

    def list_by_project(self, project_key: str) -> list[Job]:
        return [deepcopy(job) for job in self._items.values() if job.project_key == project_key]


class InMemoryRunRepository:
    def __init__(self) -> None:
        self._items: dict[str, Run] = {}

    def create(self, run: Run) -> Run:
        self._items[run.run_id] = deepcopy(run)
        return deepcopy(run)

    def get(self, run_id: str) -> Run | None:
        run = self._items.get(run_id)
        return deepcopy(run) if run else None

    def update(self, run: Run) -> Run:
        self._items[run.run_id] = deepcopy(run)
        return deepcopy(run)

    def list_by_job(self, job_id: str) -> list[Run]:
        runs = [deepcopy(run) for run in self._items.values() if run.job_id == job_id]
        return sorted(runs, key=lambda item: item.retry_count)


class InMemoryEventRepository:
    def __init__(self) -> None:
        self._items: list[Event] = []

    def append(self, event: Event) -> Event:
        stored = deepcopy(event)
        self._items.append(stored)
        return deepcopy(stored)

    def list_for_entity(self, project_key: str, entity_type: str, entity_id: str) -> list[Event]:
        return [
            deepcopy(event)
            for event in self._items
            if event.project_key == project_key
            and event.entity_type == entity_type
            and event.entity_id == entity_id
        ]
