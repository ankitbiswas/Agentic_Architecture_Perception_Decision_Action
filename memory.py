import re
from schemas import MemoryItem

def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alphanumeric, drop short tokens."""
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1}


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


class Memory:
    """Stores and retrieves agent memories across cycles and runs."""

    def __init__(self) -> None:
        self._store: list[MemoryItem] = []

    def add(self, item: MemoryItem) -> None:
        self._store.append(item)

    def read(self, query: str, top_k: int = 10) -> list[MemoryItem]:
        """Return memory items relevant to query."""
        if not self._store:
            return []

        query_tokens = _tokenize(query)

        scored: list[tuple[float, MemoryItem]] = []

        for item in self._store:
            item_tokens = _tokenize(item.descriptor)
            for kw in item.keywords:
                item_tokens.update(_tokenize(kw))
            score = _jaccard(query_tokens, item_tokens)
            if score > 0.0:
                scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def get(self, namespace: str) -> list[MemoryItem]:
        return [item for item in self._store if item.source == namespace]

    def clear(self) -> None:
        self._store.clear()
