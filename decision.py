import json
import os
from dotenv import load_dotenv
from openai import OpenAI
from artifact import ArtifactStore
from schemas import Goal, MemoryItem, DecisionOutput, ToolCall

load_dotenv()

DECISION_PROMPT = """\
You are an agent that decides how to satisfy a single goal. You have access to
tools (listed below) and can also answer directly if no tool is needed.

Return a JSON object with exactly one of these two shapes:

1. Direct answer (goal needs no tool):
   {"answer": "your answer here", "tool_call": null, "kind": "<kind>","keywords": [<kw1>, <kw2>, ...]}

2. Tool call (goal requires a tool):
   {"answer": null, "tool_call": {"name": "<tool_name>", "arguments": {<args>}}, "kind": null}

The "kind" field classifies the answer for memory storage:
- "fact": objective information, data, dates, names, results
- "preference": user preferences, likes, dislikes, style choices
- "scratchpad": intermediate reasoning, partial work, notes

RULES:
- If the goal can be answered from the provided memory hits or context or the
  LLM's own general knowledge, return a direct answer with the appropriate kind.
- If the goal requires fetching data, searching, reading files, or any external
  action, return a tool_call with the appropriate tool name and arguments.
- Use only tools from the provided tool list. Match the tool's input schema.
- If artifacts are attached to the goal (attach_artifact_ids is non-empty),
  their full contents are provided in the "artifact_content" field below,
  delimited by "--- BEGIN art:xxx ---" / "--- END art:xxx ---" markers.
  Use them to answer directly instead of making new tool calls.
- When the goal asks you to pick, compare, rank, or choose between options,
  you MUST base your answer on specific items found in the memory hits or
  history. Reference them by name. Do not give generic advice — use the
  concrete data from prior answers.
- TOOL CHOICE: When the goal says "fetch a URL" or "fetch a page", use
  fetch_url (not web_search). web_search is for open-ended queries without
  a specific URL. If a URL is provided, always use fetch_url to get the
  full page content.
- CROSS-REFERENCING: When the goal asks what multiple sources "agree on" or
  asks for "common" advice across articles, you MUST only include points that
  appear in EVERY source. Cite all agreeing sources per point. Do NOT list
  advice that only appears in one source. If artifact_content contains
  multiple articles, compare them and find the intersection.
"""


class Decider:
    """Decides what action to take for the next unfinished goal. Calls an LLM
    to choose between answering directly or invoking a tool."""

    def __init__(self, artifacts: ArtifactStore, model: str = "gpt-4o") -> None:
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self._model = model
        self._artifacts = artifacts

    def decide(
        self,
        goal: Goal,
        hits: list[MemoryItem],
        history: list[dict],
        tools: list[dict],
    ) -> DecisionOutput:
        # Build tool descriptions for the prompt
        tool_descriptions = "\n".join(
            f"- {t['name']}: {t['description']}\n  schema: {json.dumps(t['input_schema'])}"
            for t in tools
        )

        # Gather relevant context from memory hits
        hit_context = [
            {"descriptor": h.descriptor , "memory_value": h.value}
            for h in hits
        ]
        hist = [] 
        for his in history:
            hist.append({
                    "iteration": his.get("iteration"),
                    "goal_id": his.get("goal_id"),
                    "goal_text": his.get("goal_text"),
                    "action": his.get("action"),
                    "arguments": his.get("arguments"),
                    "result": his.get("result"),
                })


        user_message = {
            "goal": goal.text,
            "attach_artifact_ids": goal.attach_artifact_ids,
            "history": hist,
            "memory_hits": hit_context,
        }

        # Resolve ALL attached artifacts into a single artifact_content blob.
        # Each artifact is prefixed with its ID so the LLM can tell them apart.
        artifact_parts: list[str] = []
        for aid in goal.attach_artifact_ids:
            art = self._artifacts.get(aid)
            if art:
                raw = art.raw_bytes
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                artifact_parts.append(f"--- BEGIN {aid} ---\n{raw[:8000]}\n--- END {aid} ---")

        user_message["artifact_content"] = "\n\n".join(artifact_parts) if artifact_parts else None

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": DECISION_PROMPT},
                {"role": "user", "content": f"TOOLS:\n{tool_descriptions}\n\nINPUT:\n{json.dumps(user_message, indent=2)}"},
            ],
            temperature=0.2,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "decision_output",
                    "schema": DecisionOutput.model_json_schema(),
                },
            },
            
        )
        raw = response.choices[0].message.content or "{}"
        return DecisionOutput.model_validate_json(raw)
