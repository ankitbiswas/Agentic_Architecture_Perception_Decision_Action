import os
from dotenv import load_dotenv
from openai import OpenAI
from artifact import ArtifactStore
from schemas import MemoryItem, Goal, Observation

load_dotenv()


GOAL_DECOMPOSITION_PROMPT = """\
You are an agent that maintains a goal list. Your job is to decompose user
queries into concrete, executable goals and mark them done as work progresses.

INPUT: user query, memory hits (with artifact indices), run history, prior goal list.

RULES:
1. If prior_goals is empty: decompose the query into 1-5 bounded goals. Each
   goal is a short imperative statement. Be specific — include URLs, counts,
   and concrete criteria from the query.
2. For each prior goal: examine the run history. Mark it done:true the moment
   history contains an action that satisfies it. Once done, stays done.
3. For the first unfinished goal: if it needs raw bytes from previously
   fetched artifacts, set attach_artifact_ids to the list of artifact handles
   (e.g. ["art:abc123", "art:def456"]). Otherwise leave as empty list [].
4. Preserve goal order. Do not reorder, insert in middle, or drop goals.
5. Use artifact_index (integer) to reference memory hit artifacts. The system
   maps indices to actual handles.

CRITICAL — DYNAMIC RE-DECOMPOSITION:
6. When a goal says "read the top N results" or "fetch the top N" and the
   attached artifact_content contains search results with URLs, you MUST
   REPLACE that single goal with N concrete fetch goals — one per URL.
   Extract the exact URLs from the artifact_content. Then add a final
   synthesis goal. Example:

   BEFORE (vague):
     g0: "Search for X" (done: true)
     g1: "Read the top 3 results" (done: false, attach_artifact_ids: ["art:xyz"])

   AFTER (concrete), given artifact_content has urls A, B, C:
     g0: "Search for X" (done: true)
     g1: "Fetch and read <URL-A>" (done: false)
     g2: "Fetch and read <URL-B>" (done: false)
     g3: "Fetch and read <URL-C>" (done: false)
     g4: "Synthesize findings from the 3 fetched articles" (done: false)

7. When a later goal depends on the output of an earlier goal, phrase it to
   explicitly reference that dependency. Use phrases like "from the X found
   above" or "based on the Y retrieved earlier" so the decision layer knows
   to look at prior answers in memory.

8. SYNTHESIS GOAL ARTIFACT ATTACHMENT: When a synthesis goal (e.g. "identify
   advice agreed upon across the N articles") follows completed fetch_url
   goals, set attach_artifact_ids to ALL artifact_ids from the completed
   fetch actions in history. The decider will receive all of them and can
   cross-reference their contents.

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
Artifact index values: [{"index": 0, "artifact_id": "art:abc123", "descriptor": "fetched docs content"}]
Output goals:
  - "Fetch the latest docs" (done: true)
  - "Summarize the fetched docs" (done: false, attach_artifact_ids: ["art:abc123"])

--- Dynamic re-decomposition (read top N) ---
Query: "Search for Python tips, read the top 2 results, and summarize"
Prior goals: ["Search for Python tips" (done: true), "Read the top 2 results" (done: false, attach_artifact_ids: ["art:def456"])]
Artifact index values: [{"index": 0, "artifact_id": "art:def456", "descriptor": "search results for Python tips"}]
Artifact content for art:def456: "[{\"title\": \"10 Python Tips\", \"url\": \"https://example.com/tips\"}, {\"title\": \"Python Best Practices\", \"url\": \"https://example.com/best\"}]"
Output goals:
  - "Search for Python tips" (done: true)
  - "Fetch and read https://example.com/tips" (done: false)
  - "Fetch and read https://example.com/best" (done: false)
  - "Summarize findings from the 2 fetched articles" (done: false)

--- Synthesis goal artifact attachment ---
Query: "Search for Python tips, read the top 2 results, and summarize"
Prior goals: ["Search for Python tips" (done: true), "Fetch and read https://example.com/tips" (done: true), "Fetch and read https://example.com/best" (done: true), "Summarize findings from the 2 fetched articles" (done: false)]
History: [..., {"action": "fetch_url", "result": "...", "artifact_id": "art:aaa111"}, {"action": "fetch_url", "result": "...", "artifact_id": "art:bbb222"}]
Output goals:
  - "Search for Python tips" (done: true)
  - "Fetch and read https://example.com/tips" (done: true)
  - "Fetch and read https://example.com/best" (done: true)
  - "Summarize findings from the 2 fetched articles" (done: false, attach_artifact_ids: ["art:aaa111", "art:bbb222"])
"""



class Perceiver:
    """Maintains state across iterations. Decomposes query into goals, tracks
    completion, and attaches artifacts to the next unfinished goal."""

    def __init__(self, artifacts: ArtifactStore, model: str = "gpt-4o") -> None:
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self._model = model
        self._artifacts = artifacts

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
        artifact_index_values = [
            {"index": i, "artifact_id": hit.artifact_id, "descriptor": hit.descriptor}
            for i, hit in enumerate(hits)
            if hit.artifact_id
        ]

        user_message = {
            "query": query,
            "artifact_index_values": artifact_index_values,
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
                attach_artifact_ids=goal.attach_artifact_ids,
            ))
        return result
