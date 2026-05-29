import hashlib
from schemas import Artifact


class ArtifactStore:
    """Stores large tool outputs as artifacts, keyed by content hash."""

    def __init__(self) -> None:
        self._store: dict[str, Artifact] = {}

    def put(self, content_type: str, source: str, descriptor: str, raw_bytes: bytes) -> Artifact:
        sha = hashlib.sha256(raw_bytes).hexdigest()
        artifact_id = f"art:{sha[:16]}"
        artifact = Artifact(
            id=artifact_id,
            content_type=content_type,
            size_bytes=len(raw_bytes),
            source=source,
            descriptor=descriptor,
            raw_bytes=raw_bytes,
        )
        self._store[artifact_id] = artifact
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        return self._store.get(artifact_id)