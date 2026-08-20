"""Agent runtime — executes tasks on behalf of personas."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from autonomous.database import Repository
from autonomous.events import Event, bus
from services import config as cfg
from services import llm


class AgentRuntime:
    def __init__(self, repo: Repository):
        self.repo = repo

    async def execute(self, mission_id: str, task_id: str, persona: Dict[str, Any], goal: str, title: str) -> Dict[str, Any]:
        system = persona.get("system_prompt", "") or ""
        skills = persona.get("skills", "") or ""
        if skills:
            system += "\n\n" + skills
        system += "\n\nReply in the first person as that one persona only. Do NOT narrate, quote, or speak for any other agent."
        user = f"Subtask: {title}\nGoal: {goal}\nReply in first person as {persona.get('name','agent')} only."
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        text = ""
        async for chunk in llm.stream_chat(
            cfg.CONFIG["ollama"]["default_model"],
            msgs,
            cfg.CONFIG["ollama"]["base_url"],
            timeout=120,
        ):
            if chunk.get("content"):
                text += chunk["content"]
            if chunk.get("done"):
                break
        if not text.strip() or len(text.strip()) < 20:
            return {"status": "failed", "summary": "empty or too-short response", "findings": [], "confidence": 0.0}
        result = {"status": "completed", "summary": text, "findings": [], "confidence": 0.9}
        await bus.publish(Event(
            mission_id=mission_id,
            task_id=task_id,
            agent=persona.get("name", "unknown"),
            event="task.completed",
            data={"result": result},
        ))
        return result
