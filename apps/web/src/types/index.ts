export type UserRole = "user" | "admin" | "super_admin";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "suspended";

export type EffectiveState =
  | "unverified"
  | "pending"
  | "rejected"
  | "suspended"
  | "contribution_required"
  | "admin";

export interface UserProfile {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
  approval_status: ApprovalStatus;
  created_at: string;
  updated_at: string;
}

export interface AccessStateResponse {
  effective_state: EffectiveState;
  email_verified: boolean;
  role: UserRole;
  approval_status: ApprovalStatus;
  has_pending_request: boolean;
}

export interface OverviewResponse {
  total_users: number;
  pending_requests: number;
  approved_users: number;
  suspended_users: number;
  rejected_users: number;
  active_users_now: number;
  active_users_recent: number;
}

export interface UserAdminResponse {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
  approval_status: ApprovalStatus;
  created_at: string;
  updated_at: string;
}

export interface PaginatedUsersResponse {
  items: UserAdminResponse[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface AccessRequestResponse {
  id: string;
  user_id: string;
  email?: string;
  display_name?: string;
  status: "pending" | "approved" | "rejected";
  decision_reason?: string;
  reviewed_by?: string;
  reviewed_at?: string;
  created_at: string;
  updated_at: string;
}

export interface PaginatedAccessRequestsResponse {
  items: AccessRequestResponse[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface PresenceUserResponse {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  last_seen_at: string;
  is_active: boolean;
  client_info: Record<string, unknown>;
}

export interface PresenceListResponse {
  active_users: PresenceUserResponse[];
  total_active: number;
}

export interface AuditLogResponse {
  id: string;
  actor_id?: string;
  action: string;
  target_type: string;
  target_id?: string;
  payload: Record<string, unknown>;
  created_at: string;
}

// =============================================================================
// Milestone M2: LLM Providers & Owner Credential Management
// =============================================================================

export interface LLMProvider {
  id: string;
  name: string;
  runtime_type: "cloud_api" | "local_runtime";
  description?: string | null;
  is_enabled: boolean;
  requires_account_scope: boolean;
  allows_community_sharing: boolean;
  account_tier: "free" | "pay_as_you_go" | "enterprise" | "standard";
  billing_status: "active" | "exhausted" | "delinquent" | "unknown";
  created_at: string;
  updated_at: string;
}

export type CredentialStatus = "active" | "disabled" | "revoked";
export type ValidationStatus =
  | "unvalidated"
  | "valid"
  | "invalid"
  | "expired"
  | "rate_limited"
  | "error";
export type CredentialSharingScope = "owner_only" | "community_shared";

export interface ApiKeyCredential {
  id: string;
  provider_id: string;
  nickname: string;
  masked_key: string;
  status: CredentialStatus;
  sharing_scope: CredentialSharingScope;
  shared_consent: boolean;
  sharing_consent_timestamp?: string | null;
  consent_version?: string | null;
  validation_status: ValidationStatus;
  last_validated_at?: string | null;
  validation_error_code?: string | null;
  account_scope_id?: string | null;
  daily_request_limit: number;
  monthly_request_limit: number;
  daily_spending_limit_cents?: number | null;
  monthly_spending_limit_cents?: number | null;
  created_at: string;
  updated_at: string;
}

export interface CredentialListResponse {
  credentials: ApiKeyCredential[];
  total: number;
}

export interface CredentialCreateInput {
  provider_id: string;
  nickname: string;
  api_key: string;
  account_scope_id?: string;
  sharing_scope?: CredentialSharingScope;
  shared_consent?: boolean;
  daily_request_limit?: number;
  monthly_request_limit?: number;
  daily_spending_limit_cents?: number | null;
  monthly_spending_limit_cents?: number | null;
}

export interface CredentialUpdateInput {
  nickname?: string;
  account_scope_id?: string | null;
  sharing_scope?: CredentialSharingScope;
  shared_consent?: boolean;
  daily_request_limit?: number;
  monthly_request_limit?: number;
  daily_spending_limit_cents?: number | null;
  monthly_spending_limit_cents?: number | null;
}

export interface ValidationResult {
  is_valid: boolean;
  outcome: "valid" | "invalid_key" | "quota_exhausted" | "rate_limited" | "network_error" | "unsupported_provider";
  http_status?: number | null;
  error_code?: string | null;
  diagnostic_message: string;
  probed_at: string;
}
