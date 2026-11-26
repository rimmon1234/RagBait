"""RagBait Knowledge Base Hybrid Retrieval and Subject Scoping Module (M4.4)."""

from app.modules.knowledge_base.retrieval.authorization import verify_retrieval_authorization
from app.modules.knowledge_base.retrieval.config import RetrievalSettings, retrieval_settings
from app.modules.knowledge_base.retrieval.query_service import prepare_query_vector
from app.modules.knowledge_base.retrieval.ranking import (
    RRFParameterError,
    compute_rrf_score,
    fuse_ranked_candidates,
    validate_rrf_parameters,
)
from app.modules.knowledge_base.retrieval.retrieval_repository import search_subject_chunks
from app.modules.knowledge_base.retrieval.retrieval_service import RetrievalService
from app.modules.knowledge_base.retrieval.schemas import (
    RetrievalQuery,
    RetrievalResult,
    RetrievedChunk,
)

__all__ = [
    "RetrievalService",
    "RetrievedChunk",
    "RetrievalQuery",
    "RetrievalResult",
    "RetrievalSettings",
    "retrieval_settings",
    "RRFParameterError",
    "validate_rrf_parameters",
    "compute_rrf_score",
    "fuse_ranked_candidates",
    "verify_retrieval_authorization",
    "prepare_query_vector",
    "search_subject_chunks",
]
