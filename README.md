# Agent6

Goal-driven AI agent with perception, memory, decision, and action layers. Decomposes user queries into ordered goals, iterates until all goals are satisfied, using MCP tools for external actions and LLM calls for reasoning.

## Architecture

```
User Query
    │
    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Perception  │────▶│   Decision   │────▶│    Action    │────▶│   Memory     │
│  (observe)   │     │  (decide)    │     │  (execute)   │     │  (read/add)  │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
       │                     │                    │                    │
       │             ┌───────┘                    │                    │
       ▼             ▼                            ▼                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                           ArtifactStore (shared)                         │
│  Content-addressed storage for large tool outputs. Perception, Decision, │
│  and Action all share the same instance — artifacts stored by Action are │
│  immediately visible to Perception and Decision.                         │
└──────────────────────────────────────────────────────────────────────────┘
       │
       └────────────────────────────────────────────────────────────────────┘
                                    │
                              agent6.py loop
```

## Modules

| File | Role |
|------|------|
| `schemas.py` | Pydantic models: `MemoryItem`, `Goal`, `Observation`, `Artifact`, `DecisionOutput`, `ToolCall` |
| `perception.py` | `Perceiver.observe()` — calls LLM to decompose query into goals, mark goals done, attach artifacts. Handles dynamic re-decomposition when search results arrive. |
| `memory.py` | `Memory` — stores `MemoryItem` records, retrieves by Jaccard keyword similarity against query + history |
| `decision.py` | `Decider.decide()` — calls LLM to pick: direct answer or MCP tool call for the current goal. Resolves attached artifacts and injects their content into the prompt. |
| `action.py` | `Actor.execute()` — runs MCP tool via session, stores large results (>4KB) as artifacts, returns `(result_text, artifact_id_or_none)` |
| `artifact.py` | `ArtifactStore` — content-addressed storage for large tool outputs (SHA256 hash as ID). Shared across Perception, Decision, and Action. |
| `agent6.py` | Main loop: memory → perception → decision → action → record → repeat |
| `mcp_server.py` | MCP server with 9 tools: `web_search`, `fetch_url`, `get_time`, `currency_convert`, `read_file`, `list_dir`, `create_file`, `update_file`, `edit_file` |

## Flow

1. **Load long-term memory** — restore facts, preferences, and past answers from previous runs into current memory
2. **Memory read** — retrieve relevant past facts, tool outcomes, and history entries via Jaccard keyword matching
3. **Perception** — LLM receives query + memory hits + history + prior goals + artifact contents, returns updated `Observation` with done flags and artifact attachments. Key behaviors:
   - **Dynamic re-decomposition**: When a goal says "read the top N results" and the attached artifact contains search results with URLs, the perceiver replaces that single vague goal with N concrete `"Fetch and read <URL>"` goals + a final synthesis goal
   - **Synthesis artifact attachment**: When a synthesis goal follows completed fetch goals, `attach_artifact_ids` is set to ALL artifact IDs from the completed fetches
   - **Artifact content injection**: All relevant artifact contents are resolved and passed to the LLM so it can see URLs, search results, and fetched article text
4. **Check completion** — if all goals done, create a synthetic summary goal and send to decision for final synthesis
5. **Decision** — LLM picks first unfinished goal, returns either a direct answer or a tool call. Key behaviors:
   - **Artifact resolution**: Iterates all IDs in `goal.attach_artifact_ids`, resolves each from the shared `ArtifactStore`, concatenates with `--- BEGIN/END ---` delimiters
   - **Cross-referencing**: When the goal asks what sources "agree on," only outputs points found in EVERY source, citing all agreeing sources
   - **Tool choice**: Uses `fetch_url` for specific URLs, `web_search` for open-ended queries
6. **Action** — if tool call, execute MCP tool; large results (>4KB) stored as artifacts with preview. Returns `(result_text, artifact_id_or_none)`
7. **Record** — append to history (with `artifact_id`), store answer/tool outcome in memory
8. **Final summary** — when all goals done, decision LLM synthesizes all findings into a cohesive response. Final answer stored in long-term memory for future runs
9. Repeat until all goals done or max iterations (10)

## Key Features

- **Goal decomposition** — LLM breaks complex queries into ordered, bounded goals with explicit dependencies
- **Dynamic re-decomposition** — vague goals like "read the top 3 results" are replaced with concrete per-URL fetch goals once search results arrive
- **Stateful perception** — goals persist across iterations with stable IDs; done flags and artifact attachments updated each cycle
- **Shared ArtifactStore** — Perception, Decision, and Action share one store instance; artifacts stored by Action are immediately visible to Perception and Decision
- **Multi-artifact synthesis** — `Goal.attach_artifact_ids` (list) allows synthesis goals to receive all relevant fetched article contents at once
- **Cross-referencing** — decision prompt instructs LLM to find intersection (not union) when asked what sources "agree on"
- **Cross-run memory** — facts and preferences persist across sessions via `long_memory` list; restored on each run
- **Artifact system** — large tool outputs (>4KB) stored by content hash, referenced by ID in memory and goals
- **Keyword memory retrieval** — Jaccard similarity over tokenized descriptors + keywords, searches both memory store and history
- **MCP tool integration** — 9 sandboxed tools via stdio transport (web search, fetch, file ops, time, currency)
- **Wikipedia API optimization** — `fetch_url` detects Wikipedia URLs and uses the Wikipedia API directly for clean plaintext extracts (no HTML parsing needed)
- **Final synthesis** — when all goals complete, decision LLM summarizes all findings into one cohesive user-facing response
- **Structured LLM output** — `response_format` with `json_schema` enforces valid Pydantic model output

## Packages

```
openai
python-dotenv
mcp
httpx
ddgs
tavily-python
html2text
```

## Setup

```bash
pip install -r requirements.txt
```

Create `.env`:
```
OPENAI_API_KEY=your-key-here
TAVILY_API_KEY=your-key-here   # optional, falls back to DuckDuckGo
```

## Run

```bash
python agent6.py
```

## Example

**Query:** `"Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory"`

**Iteration 1:**
- Perception decomposes into: `["Fetch the Wikipedia page", "Extract birth date", "Extract death date", "Identify three key contributions"]`
- Decision picks goal 0 → tool_call: `fetch_url(url="https://en.wikipedia.org/wiki/Claude_Shannon")`
- Action executes MCP tool → Wikipedia API returns clean plaintext extract (~33KB, stored as artifact `art:abc123...`)
- History records: `{action: "fetch_url", result: "[artifact art:abc123, 33752 bytes] preview: ..."}`

**Iteration 2:**
- Memory read returns the tool outcome from iteration 1
- Perception sees history has `fetch_url` → marks goal 0 done, sets `attach_artifact_ids: ["art:abc123"]` on goal 1
- Decision picks goal 1, sees artifact content with birth date → answer: `"Claude Shannon was born on April 30, 1916"`
- History records answer, memory stores as fact

**Iteration 3-4:**
- Similar flow for death date and contributions goals
- Each goal sees the artifact content and extracts its specific piece of information

**Iteration 5:**
- All goals done → decision synthesizes final summary: `"Claude Shannon was born April 30, 1916 and died February 24, 2001. Three key contributions: 1) Founded information theory with 'A Mathematical Theory of Communication' (1948), 2) Introduced the concept of entropy as a measure of information, 3) Pioneered the use of Boolean algebra for digital circuit design."`

---

**Query:** `"Search for 'Python asyncio best practices', read the top 2 results, and give me a short numbered list of the advice they agree on."`

**Iteration 1:**
- Perception decomposes into: `["Search for 'Python asyncio best practices'", "Read the top 2 results", "Provide a short numbered list of agreed advice"]`
- Decision → `web_search("Python asyncio best practices")` → 5 results stored as artifact

**Iteration 2:**
- Perception sees search artifact with URLs → dynamic re-decomposition replaces "Read the top 2 results" with:
  - `"Fetch and read https://discuss.python.org/t/asyncio-best-practices/12576"`
  - `"Fetch and read https://www.shanechang.com/p/python-asyncio-best-practices-pitfalls"`
  - `"Provide a short numbered list of the advice agreed upon in the 2 articles"`
- Decision → `fetch_url` on first article

**Iteration 3:**
- Decision → `fetch_url` on second article → stored as artifact `art:612c...`

**Iteration 4:**
- Perception sets `attach_artifact_ids: ["art:03c9...", "art:612c..."]` on synthesis goal
- Decision sees both article contents delimited by `--- BEGIN/END ---` markers
- Cross-referencing rule triggers → LLM finds intersection, outputs only advice present in BOTH articles
- Answer: `"1. Use asyncio.run() as main entry point (agreed by both). 2. Always await coroutines (agreed by both). 3. Handle cancellation gracefully (agreed by both)."`
