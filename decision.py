import json
import os
from dotenv import load_dotenv
from openai import OpenAI
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
- If an artifact is attached to the goal, its content is available in the
  memory hits — use it to answer directly when possible.
- When the goal asks you to pick, compare, rank, or choose between options,
  you MUST base your answer on specific items found in the memory hits or
  history. Reference them by name. Do not give generic advice — use the
  concrete data from prior answers.
"""


class Decider:
    """Decides what action to take for the next unfinished goal. Calls an LLM
    to choose between answering directly or invoking a tool."""

    def __init__(self, model: str = "gpt-4o") -> None:
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self._model = model

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
            {"descriptor": h.descriptor}
            for h in hits
        ]

        user_message = {
            "goal": goal.text,
            "attach_artifact_id": goal.attach_artifact_id,
            "history": history,
            "memory_hits": hit_context,
        }

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
