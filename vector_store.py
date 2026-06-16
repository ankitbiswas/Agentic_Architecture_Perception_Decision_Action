import os
import numpy as np
import faiss
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

EMBEDDING_DIM = 1536  # text-embedding-3-small
CHUNK_SIZE = 300


class VectorStore:
    """FAISS-backed vector store with OpenAI embeddings. Stores vectors + metadata
    for semantic similarity search. Cosine similarity via normalized inner product."""

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self._model = model
        self._index = faiss.IndexFlatIP(EMBEDDING_DIM)
        self._metadata: list[dict] = []

    def add(self, text: str, metadata: dict) -> None:
        """Chunk `text` into CHUNK_SIZE pieces, embed each, store in FAISS."""
        if not text:
            return
        chunks = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
        vectors = []
        for i, chunk in enumerate(chunks):
            vec = self._embed(chunk)
            vec = vec / np.linalg.norm(vec)
            vectors.append(vec)
            meta = dict(metadata)
            meta["chunk_index"] = i
            meta["chunk_total"] = len(chunks)
            meta["chunk_text"] = chunk
            self._metadata.append(meta)
        self._index.add(np.array(vectors, dtype=np.float32))

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """Return top_k metadata dicts ranked by cosine similarity to query."""
        if self._index.ntotal == 0:
            return []
        query_vec = self._embed(query)
        query_vec = query_vec / np.linalg.norm(query_vec)
        scores, indices = self._index.search(
            np.array([query_vec], dtype=np.float32), min(top_k, self._index.ntotal)
        )
        results: list[dict] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            meta = dict(self._metadata[idx])
            meta["score"] = float(score)
            results.append(meta)
        return results

    def _embed(self, text: str) -> np.ndarray:
        resp = self._client.embeddings.create(model=self._model, input=text)
        return np.array(resp.data[0].embedding, dtype=np.float32)

    def __len__(self) -> int:
        return self._index.ntotal
