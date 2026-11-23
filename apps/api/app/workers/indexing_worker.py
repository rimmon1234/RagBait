"""Top-level entry point for Knowledge Base Document Indexing Worker."""

from app.modules.knowledge_base.indexing_worker import (
    KnowledgeBaseIndexingWorker,
    main,
)

__all__ = ["KnowledgeBaseIndexingWorker", "main"]

if __name__ == "__main__":
    main()
