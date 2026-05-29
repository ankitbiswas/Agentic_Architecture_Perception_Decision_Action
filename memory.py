import re
from schemas import MemoryItem
from datetime import datetime

def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alphanumeric, drop short tokens."""
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1}


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _history_to_text(entry: dict) -> str:
    """Flatten a history entry into a searchable text blob."""
    parts = [
        entry.get("goal_text", ""),
        entry.get("action", ""),
        str(entry.get("arguments", "")),
        str(entry.get("result", "")),
    ]
    return " ".join(p for p in parts if p)


class Memory:
    """Stores and retrieves agent memories across cycles and runs."""

    def __init__(self) -> None:
        self._store: list[MemoryItem] = []

    def add(self, item: MemoryItem) -> None:
        self._store.append(item)

    def read(self, query: str, history: list[dict], top_k: int = 10) -> list[MemoryItem]:
        """Return memory items relevant to query.
        Searches across both memory store AND history entries."""
        if not self._store and not history:
            return []

        query_tokens = _tokenize(query)

        scored: list[tuple[float, MemoryItem]] = []

        # Score memory items
        for item in self._store:
            item_tokens = _tokenize(item.descriptor)
            for kw in item.keywords:
                item_tokens.update(_tokenize(kw))
            # print(f"item_tokens: {item_tokens}")
            score = _jaccard(query_tokens, item_tokens)
            if score > 0.0:
                scored.append((score, item))

        # Score history entries — wrap as MemoryItem so caller gets uniform type
        for entry in history:
            hist_text = _history_to_text(entry)
            hist_tokens = _tokenize(hist_text)
            # print(f"hist_tokens: {hist_tokens}")
            score = _jaccard(query_tokens, hist_tokens)
            if score > 0.0:
                scored.append((
                    score,
                    MemoryItem(
                        id=f"hist-{entry.get('iteration', 'x')}",
                        kind="scratchpad",
                        keywords=[],
                        descriptor=hist_text[:120],
                        value=entry,
                        artifact_id=None,
                        source="history",
                        run_id="",
                        goal_id=entry.get("goal_id"),
                        confidence=0.8,
                        created_at=datetime.now(),
                    ),
                ))

        # print("scored",scored)

        scored.sort(key=lambda x: x[0], reverse=True)

        
        return [item for _, item in scored[:top_k]]

    def get(self, namespace: str) -> list[MemoryItem]:
        return [item for item in self._store if item.source == namespace]

    def clear(self) -> None:
        self._store.clear()
