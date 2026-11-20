"""Pydantic schemas for Community Local Inference & Ollama Node Agent."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.llm_gateway.schemas import ChatMessage, GenerationParams


# ------------------------------------------------------------------------------
# 1. Pairing & Registration Schemas
# ------------------------------------------------------------------------------
class PairingCodeResponse(BaseModel):
    """Temporary code presented to user for pairing their agent."""

    pairing_code: str
    expires_at: datetime
    ttl_seconds: int


class NodePairRequest(BaseModel):
    """Request sent by agent to claim a pairing code and register public key."""

    pairing_code: str = Field(..., min_length=6, max_length=16)
    public_key: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    display_name: str = Field(..., min_length=2, max_length=64)
    agent_version: str = Field(default="0.1.0", max_length=32)
    os: str | None = Field(default=None, max_length=64)
    architecture: str | None = Field(default=None, max_length=32)
    ollama_version: str | None = Field(default=None, max_length=32)


class NodePairResponse(BaseModel):
    """Response returned to agent with registered node credentials and server verifying key."""

    node_id: UUID
    display_name: str
    status: str
    server_public_key: str
    server_key_id: str


# ------------------------------------------------------------------------------
# 2. Node Management Schemas
# ------------------------------------------------------------------------------
class CommunityNodeModelResponse(BaseModel):
    """Discovered or configured model on a community node."""

    id: UUID
    node_id: UUID
    model_name: str
    enabled: bool
    parameter_size: str | None = None
    quantization_level: str | None = None
    size_bytes: int | None = None
    last_seen_at: datetime


class CommunityNodeResponse(BaseModel):
    """Comprehensive node view for contributor."""

    id: UUID
    owner_id: UUID
    display_name: str
    public_key: str
    key_version: str
    agent_version: str
    os: str | None = None
    architecture: str | None = None
    ollama_version: str | None = None
    status: str
    sharing_enabled: bool
    max_concurrent_jobs: int
    max_execution_duration: int
    last_seen_at: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None
    models: list[CommunityNodeModelResponse] = Field(default_factory=list)


class CommunityNodeUpdateRequest(BaseModel):
    """Partial updates by node owner."""

    display_name: str | None = Field(default=None, min_length=2, max_length=64)
    sharing_enabled: bool | None = None
    max_concurrent_jobs: int | None = Field(default=None, ge=1, le=4)
    max_execution_duration: int | None = Field(default=None, ge=30, le=600)


class CommunityNodeModelUpdateItem(BaseModel):
    model_name: str
    enabled: bool


class CommunityNodeModelsUpdateRequest(BaseModel):
    models: list[CommunityNodeModelUpdateItem]


class CommunityAvailableModel(BaseModel):
    """Active community-available model for requesters."""

    model_name: str
    parameter_size: str | None = None
    online_nodes_count: int


# ------------------------------------------------------------------------------
# 3. Community Inference Job Schemas
# ------------------------------------------------------------------------------
class SignedJobAuthorization(BaseModel):
    """Server-signed authorization token carried with job dispatch."""

    job_id: UUID
    node_id: UUID
    model_name: str
    payload_digest: str
    max_output_tokens: int
    hard_deadline_ts: int
    auth_nonce: str
    signature: str
    key_id: str


class CommunityJobSubmitRequest(BaseModel):
    """Requester submission for community-hosted inference."""

    model_name: str = Field(..., min_length=2, max_length=128)
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=100)
    params: GenerationParams = Field(default_factory=GenerationParams)
    consent_accepted: bool = Field(..., description="User explicit consent for community inference")
    consent_policy_version: str = Field(default="2026-02-community-v1")


class CommunityJobSubmitResponse(BaseModel):
    """Immediate submission result acknowledging queueing."""

    job_id: UUID
    node_id: UUID
    model_name: str
    status: str
    created_at: datetime


class CommunityJobStatusResponse(BaseModel):
    """Current job status and completion metrics."""

    job_id: UUID
    node_id: UUID
    model_name: str
    status: str
    accepted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    failure_code: str | None = None
    failure_details: str | None = None
    reported_input_tokens: int | None = None
    reported_output_tokens: int | None = None
    duration_ms: int | None = None
    tokens_per_second: float | None = None
    usage_provenance: str
    created_at: datetime


# ------------------------------------------------------------------------------
# 4. WebSocket Protocol Schemas
# ------------------------------------------------------------------------------
class WSChallengeMessage(BaseModel):
    type: Literal["challenge"] = "challenge"
    nonce: str
    session_id: str
    timestamp: int


class WSAuthMessage(BaseModel):
    type: Literal["auth"] = "auth"
    node_id: str
    signature: str
    timestamp: int


class WSAuthSuccessMessage(BaseModel):
    type: Literal["auth_success"] = "auth_success"
    session_id: str
    heartbeat_interval_sec: int = 15


class WSHeartbeatMessage(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    installed_models: list[dict[str, Any]] = Field(default_factory=list)
    active_jobs: int = 0


class WSHeartbeatAckMessage(BaseModel):
    type: Literal["heartbeat_ack"] = "heartbeat_ack"
    timestamp: int


class WSJobDispatchMessage(BaseModel):
    type: Literal["job_dispatch"] = "job_dispatch"
    job_id: str
    authorization: SignedJobAuthorization
    payload: dict[str, Any]


class WSJobAcceptedMessage(BaseModel):
    type: Literal["job_accepted"] = "job_accepted"
    job_id: str


class WSJobChunkMessage(BaseModel):
    type: Literal["job_chunk"] = "job_chunk"
    job_id: str
    seq_id: int
    delta: str
    done: bool = False


class WSJobCompletedMessage(BaseModel):
    type: Literal["job_completed"] = "job_completed"
    job_id: str
    reported_input_tokens: int = 0
    reported_output_tokens: int = 0
    duration_ms: int = 0
    tokens_per_second: float = 0.0


class WSJobCancelMessage(BaseModel):
    type: Literal["job_cancel"] = "job_cancel"
    job_id: str


class WSJobCancelledAckMessage(BaseModel):
    type: Literal["job_cancelled_ack"] = "job_cancelled_ack"
    job_id: str


class WSErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str
