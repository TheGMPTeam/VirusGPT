"""Agent runtime — executes tasks on behalf of personas, with native tool use.

Uses Ollama's function-calling: the model returns `tool_calls` in its message.
The runtime executes each tool via the self-contained harness (autonomous.tools),
appends the results as `tool` role messages, and loops (ReAct) up to TOOL_ROUNDS
times. Every tool call is published to the event bus so the UI can show a live
tool-call list on the Kanban card / mission feed, and a `git_commit` tool lets a
verified agent publish its output back to the local repo ("update repo").
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from autonomous.database import Repository
from autonomous.events import Event, bus
from autonomous import tools as agent_tools
from services import config as cfg
from services import llm

TOOL_ROUNDS = 4  # max tool-call / observation cycles per task


def _ollama_tools(allowed: Optional[List[str]] = None) -> List[dict]:
    """Tools exposed to a persona. If `allowed` is set (a persona's `tools`
    array), only those tools are offered; otherwise all registered tools are."""
    tools = agent_tools.tools_for_ollama()
    if not allowed:
        return tools
    allowed_set = set(allowed)
    return [t for t in tools if t["function"]["name"] in allowed_set]


class AgentRuntime:
    def __init__(self, repo: Repository):
        self.repo = repo

    async def execute(self, mission_id: str, task_id: str, persona: Dict[str, Any], goal: str, title: str) -> Dict[str, Any]:
        system = persona.get("system_prompt", "") or ""
        skills = persona.get("skills", "") or ""
        if skills:
            system += "\n\n" + skills
        system += "\n\nReply in the first person as that one persona only. Do NOT narrate, quote, or speak for any other agent. You may call the provided tools when they help; once you have the answer, reply with the final answer only."
        user = f"Subtask: {title}\nGoal: {goal}\nReply in first person as {persona.get('name','agent')} only."
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        ollama_tools = _ollama_tools(persona.get("tools"))
        final_text = ""
        generated_images: List[str] = []  # real /api/generated/... urls from render_image

        for _ in range(TOOL_ROUNDS):
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": ""}
            tool_calls: List[dict] = []
            async for chunk in llm.stream_chat(
                cfg.CONFIG["ollama"]["default_model"],
                msgs,
                cfg.CONFIG["ollama"]["base_url"],
                timeout=120,
                tools=ollama_tools,
            ):
                if chunk.get("content"):
                    assistant_msg["content"] += chunk["content"]
                if chunk.get("tool_calls"):
                    tool_calls.extend(chunk["tool_calls"])
                if chunk.get("done"):
                    break

            # No tool requested -> this is the final answer.
            if not tool_calls:
                final_text = (assistant_msg["content"] or "").strip()
                break

            # Record the assistant turn (with its tool_calls) for context.
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            msgs.append(assistant_msg)

            # Execute every requested tool and collect observations.
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        args = json.loads(args or "{}")
                except Exception:
                    args = {}
                result = await agent_tools.run_tool(name, args)
                ok = not (isinstance(result, dict) and result.get("error"))
                # Capture real generated-image URLs so the result is correct
                # even if the model mis-states the URL in its summary.
                if name == "render_image" and isinstance(result, dict) and result.get("status") == "completed":
                    if result.get("url"):
                        generated_images.append(result["url"])
                await bus.publish(Event(
                    mission_id=mission_id, task_id=task_id,
                    agent=persona.get("name", "unknown"), event="tool.call",
                    data={"tool": name, "args": args, "result": result},
                ))
                msgs.append({
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False)[:4000],
                    "name": name,
                })
            final_text = (assistant_msg["content"] or "").strip()

        if not final_text or len(final_text) < 20:
            return {"status": "failed", "summary": "empty or too-short response", "findings": [], "confidence": 0.0}


        result = {"status": "completed", "summary": final_text, "findings": [],
                  "confidence": 0.9, "tools_used": True,
                  "generated_images": generated_images}
        await bus.publish(Event(
            mission_id=mission_id, task_id=task_id,
            agent=persona.get("name", "unknown"), event="task.completed",
            data={"result": result},
        ))
        return result
