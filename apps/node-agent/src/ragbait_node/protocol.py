"""Pydantic schemas for the RagBait Node Agent protocol."""

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    authorization: dict[str, Any]
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
