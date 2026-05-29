import json
from mcp import ClientSession
from schemas import ToolCall
from artifact import ArtifactStore

ARTIFACT_THRESHOLD_BYTES = 4096  # 4 KB


class Actor:
    """Executes MCP tool calls and handles large results via ArtifactStore."""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts

    async def execute(
        self, session: ClientSession, tool_call: ToolCall
    ) -> tuple[str, str | None]:
        """Run the MCP tool. Returns (result_text, artifact_id_or_none)."""
        result = await session.call_tool(tool_call.name, tool_call.arguments)
        text = self._extract_text(result)

        if len(text.encode("utf-8")) > ARTIFACT_THRESHOLD_BYTES:
            artifact = self._artifacts.put(
                content_type="text/plain",
                source=tool_call.name,
                descriptor=f"Output of {tool_call.name} is {text[:500]}",
                raw_bytes=text.encode("utf-8"),
            )
            preview = text[:2000]
            descriptor = (
                f"artifact size: {artifact.size_bytes} bytes "
                f"preview: {preview}"
            )
            return descriptor, artifact.id

        return text, None

    def _extract_text(self, result: object) -> str:
        """Pull a string out of an MCP CallToolResult."""
        if hasattr(result, "content") and result.content:
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "data"):
                    parts.append(str(block.data))
                else:
                    parts.append(str(block))
            return "\n".join(parts)
        return json.dumps(result, default=str)