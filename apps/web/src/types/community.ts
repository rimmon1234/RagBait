export interface PairingCodeResponse {
  pairing_code: string;
  expires_at: string;
  ttl_seconds: number;
}

export interface CommunityNodeModel {
  id: string;
  node_id: string;
  model_name: string;
  enabled: boolean;
  parameter_size?: string | null;
  quantization_level?: string | null;
  size_bytes?: number | null;
  last_seen_at: string;
}

export interface CommunityNode {
  id: string;
  owner_id: string;
  display_name: string;
  public_key: string;
  key_version: number;
  agent_version: string;
  os?: string | null;
  architecture?: string | null;
  ollama_version?: string | null;
  status: "active" | "online" | "paused" | "offline" | "revoked";
  sharing_enabled: boolean;
  max_concurrent_jobs: number;
  max_execution_duration: number;
  last_seen_at?: string | null;
  created_at: string;
  revoked_at?: string | null;
  models: CommunityNodeModel[];
}

export interface CommunityAvailableModel {
  model_name: string;
  parameter_size?: string | null;
  online_nodes_count: number;
}

export interface CommunityJobSubmitRequest {
  model_name: string;
  messages: { role: string; content: string }[];
  params?: {
    temperature?: number;
    top_p?: number;
    max_tokens?: number;
  };
  consent_accepted: boolean;
  consent_policy_version?: string;
}

export interface CommunityJobSubmitResponse {
  job_id: string;
  node_id: string;
  model_name: string;
  status: string;
  created_at: string;
}

export interface CommunityJobStatusResponse {
  job_id: string;
  node_id: string;
  model_name: string;
  status: "QUEUED" | "ASSIGNED" | "IN_PROGRESS" | "COMPLETED" | "FAILED" | "CANCELLED" | "TIMEOUT";
  accepted_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  cancelled_at?: string | null;
  failure_code?: string | null;
  failure_details?: string | null;
  reported_input_tokens?: number | null;
  reported_output_tokens?: number | null;
  reported_duration_ms?: number | null;
}

export interface CommunityStreamChunk {
  chunk_index: number;
  delta: string;
  done: boolean;
  finish_reason?: string | null;
  prompt_eval_count?: number | null;
  eval_count?: number | null;
  total_duration_ms?: number | null;
  error?: string | null;
}
