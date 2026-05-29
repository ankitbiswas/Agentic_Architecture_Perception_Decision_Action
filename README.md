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
       └─────────────────────┴────────────────────┴────────────────────┘
                                    │
                              agent6.py loop
```

## Modules

| File | Role |
|------|------|
| `schemas.py` | Pydantic models: `MemoryItem`, `Goal`, `Observation`, `Artifact`, `DecisionOutput`, `ToolCall` |
| `perception.py` | `Perceiver.observe()` — calls LLM to decompose query into goals, mark goals done, attach artifacts |
| `memory.py` | `Memory` — stores `MemoryItem` records, retrieves by Jaccard keyword similarity against query + history |
| `decision.py` | `Decider.decide()` — calls LLM to pick: direct answer or MCP tool call for the current goal |
| `action.py` | `Actor.execute()` — runs MCP tool via session, stores large results (>4KB) as artifacts |
| `artifact.py` | `ArtifactStore` — content-addressed storage for large tool outputs (SHA256 hash as ID) |
| `agent6.py` | Main loop: memory → perception → decision → action → record → repeat |
| `mcp_server.py` | MCP server with 9 tools: `web_search`, `fetch_url`, `get_time`, `currency_convert`, `read_file`, `list_dir`, `create_file`, `update_file`, `edit_file` |

## Flow

1. **Load long-term memory** — restore facts, preferences, and past answers from previous runs into current memory
2. **Memory read** — retrieve relevant past facts, tool outcomes, and history entries via Jaccard keyword matching
3. **Perception** — LLM receives query + memory hits + history + prior goals, returns updated `Observation` with done flags and artifact attachments. Dependent goals explicitly reference prior outputs (e.g. "from the 3 found above")
4. **Check completion** — if all goals done, create a synthetic summary goal and send to decision for final synthesis
5. **Decision** — LLM picks first unfinished goal, returns either a direct answer or a tool call. When comparing/picking, must reference specific items from memory hits
6. **Action** — if tool call, execute MCP tool; large results (>4KB) stored as artifacts with preview
7. **Record** — append to history, store answer/tool outcome in memory
8. **Final summary** — when all goals done, decision LLM synthesizes all findings into a cohesive response. Final answer stored in long-term memory for future runs
9. Repeat until all goals done or max iterations (10)

## Key Features

- **Goal decomposition** — LLM breaks complex queries into ordered, bounded goals with explicit dependencies
- **Stateful perception** — goals persist across iterations with stable IDs; done flags and artifact attachments updated each cycle
- **Cross-run memory** — facts and preferences persist across sessions via `long_memory` list; restored on each run
- **Artifact system** — large tool outputs (>4KB) stored by content hash, referenced by ID in memory
- **Keyword memory retrieval** — Jaccard similarity over tokenized descriptors + keywords, searches both memory store and history
- **MCP tool integration** — 9 sandboxed tools via stdio transport (web search, fetch, file ops, time, currency)
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
crawl4ai
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
- Perception decomposes into: `["Fetch the Wikipedia page", "Extract birth date, death date, and three contributions"]`
- Decision picks goal 0 → tool_call: `fetch_url(url="https://en.wikipedia.org/wiki/Claude_Shannon")`
- Action executes MCP tool → returns page content (large → stored as artifact `art:abc123...`)
- History records: `{action: "fetch_url", result: "[artifact art:abc123, 263507 bytes] preview: ..."}`

**Iteration 2:**
- Memory read returns the tool outcome from iteration 1
- Perception sees history has `fetch_url` → marks goal 0 done, attaches `art:abc123` to goal 1
- Decision picks goal 1, sees attached artifact in memory hits → answer: `"Claude Shannon born April 30, 1916, died February 24, 2001. Key contributions: information theory, Shannon-Hartley theorem, digital circuit design."`
- History records answer, memory stores as fact
- All goals done → decision synthesizes final summary from all answers and returns to user
