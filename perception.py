import os
from dotenv import load_dotenv
from openai import OpenAI
from schemas import MemoryItem, Goal, Observation

load_dotenv()


GOAL_DECOMPOSITION_PROMPT = """\
You are a goal tracker. Your job is to maintain a goal list and mark goals done.

INPUT: user query, memory hits (with artifact indices), run history, prior goal list.

RULES:
1. If prior_goals is empty: decompose the query into 1-5 bounded goals. Each goal is a short imperative statement.
2. For each prior goal: examine the run history. Mark it done:true the moment history contains an action that satisfies it. Once done, stays done.
3. For the first unfinished goal: if it needs raw bytes from a previously fetched artifact, set attach_artifact_id to the artifact handle (e.g. "art:abc123"). Otherwise leave null.
4. Preserve goal order. Do not reorder, insert in middle, or drop goals.
5. Use artifact_index (integer) to reference memory hit artifacts. The system maps indices to actual handles.

RULES (continued):
6. When a later goal depends on the output of an earlier goal, phrase it to
   explicitly reference that dependency. Use phrases like "from the X found
   above" or "based on the Y retrieved earlier" so the decision layer knows
   to look at prior answers in memory.

EXAMPLES:

--- Decomposition (prior_goals empty) ---
Query: "Find the latest docs and summarize them then email the summary"
Output goals:
  - "Fetch the latest docs" (done: false)
  - "Summarize the fetched docs" (done: false)
  - "Email the summary" (done: false)

--- Dependent goals (compare-and-pick pattern) ---
Query: "Find 3 family-friendly things to do in Tokyo this weekend. Check Saturday's weather and tell me which activity is most appropriate."
Output goals:
  - "Find 3 family-friendly activities in Tokyo this weekend" (done: false)
  - "Check Saturday's weather forecast in Tokyo" (done: false)
  - "Pick the most weather-appropriate activity from the 3 found in the first goal" (done: false)

--- Done marking ---
Prior goals: ["Fetch the latest docs", "Summarize the fetched docs", "Email the summary"]
History: [{"action": "web_fetch", "result": "fetched docs from url"}]
Output goals:
  - "Fetch the latest docs" (done: true)
  - "Summarize the fetched docs" (done: false)
  - "Email the summary" (done: false)

--- Artifact attachment ---
Prior goals: ["Fetch the latest docs" (done: true), "Summarize the fetched docs" (done: false)]
Artifact index: [{"index": 0, "artifact_id": "art:abc123", "descriptor": "fetched docs content"}]
Output goals:
  - "Fetch the latest docs" (done: true)
  - "Summarize the fetched docs" (done: false, attach_artifact_id: "art:abc123")
"""



class Perceiver:
    """Maintains state across iterations. Decomposes query into goals, tracks
    completion, and attaches artifacts to the next unfinished goal."""

    def __init__(self, model: str = "gpt-4o") -> None:
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self._model = model

    def observe(
        self,
        query: str,
        hits: list[MemoryItem],
        history: list[dict],
        prior_goals: list[Goal],
        run_id: str,
    ) -> Observation:
        # Every iteration: send full state to LLM. It handles decomposition,
        # done-flagging, and artifact attachment per the prompt rules.
        goals = self._call_llm(query, hits, history, prior_goals, run_id)
        return Observation(goals=goals)

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(
        self,
        query: str,
        hits: list[MemoryItem],
        history: list[dict],
        prior_goals: list[Goal],
        run_id: str,
    ) -> list[Goal]:
        # Build artifact index so LLM can reference hits by integer position
        artifact_index = [
            {"index": i, "artifact_id": hit.artifact_id, "descriptor": hit.descriptor}
            for i, hit in enumerate(hits)
            if hit.artifact_id
        ]

        user_message = {
            "query": query,
            "artifact_index": artifact_index,
            "history": history,
            "prior_goals": [g.model_dump() for g in prior_goals],
        }

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": GOAL_DECOMPOSITION_PROMPT},
                {"role": "user", "content": str(user_message)},
            ],
            temperature=0.2,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "observation",
                    "schema": Observation.model_json_schema(),
                },
            },
        )
        # print("Raw LLM output:", response.choices[0])
        raw = response.choices[0].message.content or "{}"
        
        observation = Observation.model_validate_json(raw)

        # Assign stable IDs scoped to this run, preserving prior goal IDs
        prior_map = {g.id: g for g in prior_goals}
        result: list[Goal] = []
        for i, goal in enumerate(observation.goals):
            goal_id = prior_map[goal.id].id if goal.id in prior_map else f"{run_id}-g{i}"
            result.append(Goal(
                id=goal_id,
                text=goal.text,
                done=goal.done,
                attach_artifact_id=goal.attach_artifact_id,
            ))
        return result
