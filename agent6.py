import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from perception import Perceiver
from memory import Memory
from decision import Decider
from action import Actor
from artifact import ArtifactStore
from schemas import Goal, MemoryItem

MAX_ITERATIONS = 10

SERVER_PARAMS = StdioServerParameters(
    command="python",
    args=["mcp_server.py"],
)


@asynccontextmanager
async def mcp_session():
    """Context manager that yields a live MCP ClientSession."""
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def load_tools(session: ClientSession) -> list[dict]:
    """Fetch tool definitions from the MCP server."""
    result = await session.list_tools()
    return [
        {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
        for t in result.tools
    ]


def mcp_tools_for_decision(tools: list[dict]) -> list[dict]:
    """Pass-through stub — reshape later when decision.py needs it."""
    return tools


async def run(query: str, long_memory: list[dict]|None) -> tuple[str, list[dict]]:
    """Run the agent loop for a single user query. Returns final answer."""
    run_id = uuid.uuid4().hex[:8]
    history: list[dict] = []
    prior_goals: list[Goal] = []

    artifacts = ArtifactStore()
    memory = Memory()
    perceiver = Perceiver(artifacts=artifacts)
    decider = Decider(artifacts=artifacts)
    actor = Actor(artifacts)
    decision_list =[]
    long_term_memory = long_memory if long_memory is not None and len(long_memory) > 0 else []  # Pass long-term memory as an argument and return updated version
    # # Store the user query as a durable memory fact
    # memory.add(
    #     MemoryItem(
    #         id=f"mem-{run_id}",
    #         kind="fact",
    #         keywords=[],
    #         descriptor=query,
    #         value={"raw": query},
    #         artifact_id=None,
    #         source="user_query",
    #         run_id=run_id,
    #         goal_id=None,
    #         confidence=1.0,
    #     ),
    # )

    for i, mem in enumerate(long_term_memory):
        memory.add(MemoryItem(
                        id=mem.get("id") or f"mem-{run_id}-long-{i}",
                        kind=mem.get("kind", "fact"),
                        keywords=mem.get("keywords", []),
                        descriptor=mem.get("descriptor", ""),
                        value=mem.get("value", {}),
                        artifact_id=mem.get("artifact_id"),
                        source=mem.get("source", "history"),
                        run_id=mem.get("run_id",""),
                        goal_id=mem.get("goal_id"),
                        confidence=mem.get("confidence", 1.0),
                        created_at=mem.get("created_at") or datetime.now(),
                    ))


    async with mcp_session() as session:
        mcp_tools = await load_tools(session)
        tools = mcp_tools_for_decision(mcp_tools)

        for iteration in range(1, MAX_ITERATIONS + 1):
            # 1. Memory: retrieve relevant hits
            hits = memory.read(query, history)

            # 2. Perception: observe state, update goals
            obs = perceiver.observe(query, hits, history, prior_goals, run_id)
            prior_goals = obs.goals
            print(prior_goals)
            # 3. Check if all goals done — synthesize final summary
            if all(g.done for g in prior_goals):
                summary_goal = Goal(
                    id=f"{run_id}-summary",
                    text="Summarize all findings into a cohesive final answer for the user",
                    done=False,
                    attach_artifact_ids=[],
                )
                final_decision = decider.decide(summary_goal, hits,history, tools)
                if final_decision.answer:
                        long_term_memory.append({
                        "id": f"mem-{run_id}-final",
                        "kind": final_decision.kind,
                        "keywords": final_decision.keywords,
                        "descriptor": f"Final answer for query: {query}",
                        "value": {"answer": final_decision.answer, "goal_text": summary_goal.text},
                        "artifact_id": None,
                        "source": "final_decision",
                        "run_id": run_id,
                        "goal_id": summary_goal.id,
                        "confidence": 1.0,
                        "created_at": datetime.now(),
                        })
                return final_decision.answer or "No summary produced.", long_term_memory

            # 4. Decision: pick first unfinished goal, call LLM
            next_goal = next(g for g in prior_goals if not g.done)
            decision = decider.decide(next_goal, hits, history, tools)
            print("decision tool call",decision.tool_call)
            print("decision answer",decision.answer)
            
            # 5. Record in history + update memory
            if decision.tool_call:
                result_text, art_id = await actor.execute(session, decision.tool_call)
                print(f"length of result_text: {len(result_text)}, artifact ID: {art_id}")
                history.append({
                    "iteration": iteration,
                    "goal_id": next_goal.id,
                    "goal_text": next_goal.text,
                    "action": decision.tool_call.name,
                    "arguments": decision.tool_call.arguments,
                    "result": result_text,
                    "artifact_id": art_id,
                })
                # Store tool outcome in memory
                memory.add(
                    MemoryItem(
                        id=f"mem-{run_id}-t{iteration}",
                        kind="tool_outcome",
                        keywords=[],
                        descriptor=f"Tool {decision.tool_call.name}: {result_text[:1000]}" if art_id else f"Tool {decision.tool_call.name}: {result_text[:500]}",
                        value={
                            "tool": decision.tool_call.name,
                            "arguments": decision.tool_call.arguments,
                            "result": result_text,
                        },
                        artifact_id=art_id,
                        source="tool_execution",
                        run_id=run_id,
                        goal_id=next_goal.id,
                        confidence=1.0,
                        created_at=datetime.now(),
                    ),
                )
            else:
                decision_list.append(decision.answer)
                history.append({
                    "iteration": iteration,
                    "goal_id": next_goal.id,
                    "goal_text": next_goal.text,
                    "action": "answer",
                    "result": decision.answer,
                })
                # Store answer in memory so later goals can use it
                memory.add(
                    MemoryItem(
                        id=f"mem-{run_id}-a{iteration}",
                        kind=decision.kind or "fact",
                        keywords=decision.keywords,
                        descriptor=f"Answer for: {next_goal.text}",
                        value={"answer": decision.answer, "goal_text": next_goal.text},
                        artifact_id=None,
                        source="decision_answer",
                        run_id=run_id,
                        goal_id=next_goal.id,
                        confidence=1.0,
                        created_at=datetime.now(),
                    )
                )
    return "Max iterations reached without completing all goals."


def main() -> None:
    print("Agent6 ready. Type 'quit' to exit.\n")
    long_memory = []
    while True:
        raw = input("You: ")
        if raw.lower() == "quit":
            break

        result, long_memory_result = asyncio.run(run(raw, long_memory))
        long_memory = long_memory_result
        print(f"Agent: {result}")


if __name__ == "__main__":
    main()
