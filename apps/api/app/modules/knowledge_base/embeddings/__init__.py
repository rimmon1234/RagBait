"""Knowledge base embedding package public interfaces."""

from app.modules.knowledge_base.embeddings.config import EmbeddingSettings, embedding_settings
from app.modules.knowledge_base.embeddings.model_loader import (
    ModelNotProvisionedError,
    get_embedding_model,
)
from app.modules.knowledge_base.embeddings.model_manifest import (
    ModelManifest,
    get_model_manifest,
)
from app.modules.knowledge_base.embeddings.schemas import EmbeddingResult
from app.modules.knowledge_base.embeddings.service import (
    PassageLengthExceededError,
    QueryLengthExceededError,
    embed_passages,
    embed_query,
)
from app.modules.knowledge_base.embeddings.vector_validation import (
    VectorValidationError,
    validate_vector,
)

__all__ = [
    "EmbeddingResult",
    "EmbeddingSettings",
    "ModelManifest",
    "ModelNotProvisionedError",
    "PassageLengthExceededError",
    "QueryLengthExceededError",
    "VectorValidationError",
    "embed_passages",
    "embed_query",
    "embedding_settings",
    "get_embedding_model",
    "get_model_manifest",
    "validate_vector",
]
