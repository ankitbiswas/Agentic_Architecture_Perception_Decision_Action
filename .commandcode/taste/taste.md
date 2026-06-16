# Taste (Continuously Learned by [CommandCode][cmd])

[cmd]: https://commandcode.ai/

# vector-store
- Chunk text into pieces of length 300 before embedding and storing in the FAISS vector store. Confidence: 0.65
- Vector search first, then fall back to keyword-based matching only if vector search returns no results. Confidence: 0.70
- When converting FAISS vector search results to MemoryItem, include the actual chunk text in the descriptor field (not just metadata). Confidence: 0.65

