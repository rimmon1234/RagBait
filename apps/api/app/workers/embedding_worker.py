"""Top-level entry point for Knowledge Base Document Embedding Worker."""

from app.modules.knowledge_base.embeddings.worker import (
    KnowledgeBaseEmbeddingWorker,
    main,
)

__all__ = ["KnowledgeBaseEmbeddingWorker", "main"]

if __name__ == "__main__":
    main()
