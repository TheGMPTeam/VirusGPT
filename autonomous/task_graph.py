"""Persistent task graph + state machine."""

import json, os, time, uuid
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


TASK_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "tasks")
os.makedirs(TASK_DIR, exist_ok=True)


@dataclass
class Subtask:
    task_id: str
    title: str
    agent: str
    status: str = "pending"          # pending -> running -> completed / failed
    dependencies: List[str] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    retries: int = 0
    max_retries: int = 2


@dataclass
class Task:
    id: str = field(default_factory=lambda: f"task-{int(time.time())}-{uuid.uuid4().hex[:6]}")
    goal: str = ""
    status: str = "created"          # created -> planning -> running -> reviewing -> completed / failed / blocked
    planner: str = ""
    subtasks: List[Subtask] = field(default_factory=list)
    completed_subtasks: List[str] = field(default_factory=list)
    failed_subtasks: List[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 2
    requires_approval: bool = False
    approval_state: Optional[str] = None  # pending / approved / rejected
    final_result: Optional[Dict[str, Any]] = None
    memory_namespace: str = ""
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    updated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    log: List[str] = field(default_factory=list)

    def save(self):
        path = os.path.join(TASK_DIR, f"{self.id}.json")
        data = asdict(self)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, task_id: str) -> Optional["Task"]:
        path = os.path.join(TASK_DIR, f"{task_id}.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            data = json.load(f)
        sts = [Subtask(**s) for s in data.pop("subtasks", [])]
        t = cls(**data)
        t.subtasks = sts
        return t

    @classmethod
    def recent(cls, limit: int = 20) -> List["Task"]:
        tasks = []
        for fn in sorted(os.listdir(TASK_DIR), reverse=True)[:limit]:
            if fn.endswith(".json"):
                t = cls.load(fn[:-5])
                if t:
                    tasks.append(t)
        return tasks

    def touch(self):
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
