"""Central event bus for the autonomous engine."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class Event:
    id: str = field(default_factory=lambda: f"evt-{uuid.uuid4().hex[:10]}")
    mission_id: str = ""
    task_id: str = ""
    agent: str = ""
    event: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))


class EventBus:
    def __init__(self):
        self._subscribers: List[Callable[[Event], None]] = []
        self._history: Dict[str, List[Event]] = {}

    def subscribe(self, handler: Callable[[Event], None]):
        self._subscribers.append(handler)

    async def publish(self, evt: Event):
        if evt.mission_id not in self._history:
            self._history[evt.mission_id] = []
        self._history[evt.mission_id].append(evt)
        # Persist to durable storage for audit trail across restarts.
        try:
            from autonomous.database import Repository

            Repository().add_event(
                Event(
                    id=evt.id,
                    mission_id=evt.mission_id,
                    task_id=evt.task_id,
                    agent=evt.agent,
                    event=evt.event,
                    data=json.dumps(evt.data) if isinstance(evt.data, (dict, list)) else str(evt.data or ""),
                    created_at=evt.created_at,
                )
            )
        except Exception:
            pass
        for handler in self._subscribers:
            try:
                result = handler(evt)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    def history(self, mission_id: str, limit: int = 100) -> List[Event]:
        return self._history.get(mission_id, [])[-limit:]


bus = EventBus()
