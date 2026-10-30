"""Resolve source tasks from an explicit bundle or bundled benchmark domains."""

import json

from automation_environment.simulator import TaskDefinition, strip_none_values


class TaskCatalog:
    def __init__(self, tasks: list[dict] | None = None):
        self._domains: dict[str, dict[str, TaskDefinition]] = {}
        self._explicit = tasks is not None
        for row in tasks or []:
            self.add(row)

    def add(self, row: dict) -> None:
        info = json.loads(row["info"]) if isinstance(row["info"], str) else row["info"]
        info = strip_none_values(info)
        task_name = info["task_name"]
        domain = task_name.split(".", 1)[0]
        task = TaskDefinition(domain, task_name, row.get("example_id"), row["prompt"], info)
        self._domains.setdefault(domain, {})[task_name] = task

    def _load_domain(self, domain: str) -> None:
        if domain in self._domains or self._explicit:
            return
        from automationbench.domains import get_domain_dataset

        for row in get_domain_dataset(domain):
            self.add(row)

    def get(self, domain: str, task_name: str) -> TaskDefinition:
        self._load_domain(domain)
        try:
            return self._domains[domain][task_name]
        except KeyError as error:
            raise KeyError(f"Task not found: {task_name}") from error
