import uuid
from typing import Any


class TaskTracker:
    """In-memory task tracker for background operations."""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}

    def create_task(self, task_type: str, total: int, title: str = "", **kwargs: Any) -> str:
        task_id = str(uuid.uuid4())[:8]
        self._tasks[task_id] = {
            "id": task_id,
            "type": task_type,
            "status": "running",
            "total": total,
            "current": 0,
            "current_item": title,
            "logs": [],
            "results": [],
            **kwargs,
        }
        return task_id

    def add_log(self, task_id: str, message: str) -> None:
        if task_id in self._tasks:
            self._tasks[task_id]["logs"].append(message)

    def update_progress(
        self,
        task_id: str,
        current: int,
        item_title: str,
        log_message: str | None = None,
        result_item: dict[str, Any] | None = None,
    ) -> None:
        if task_id in self._tasks:
            task = self._tasks[task_id]
            task["current"] = current
            task["current_item"] = item_title
            if log_message:
                task["logs"].append(log_message)
            if result_item:
                task["results"].append(result_item)

    def finish_task(self, task_id: str, status: str = "completed") -> None:
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = status

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)

    def get_active_task(self) -> dict[str, Any] | None:
        for task in reversed(list(self._tasks.values())):
            if task.get("status") == "running":
                return task
        return None


task_tracker = TaskTracker()
