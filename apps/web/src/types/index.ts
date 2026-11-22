export type UserRole = "user" | "admin" | "super_admin";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "suspended";

export type EffectiveState =
  | "unverified"
  | "pending"
  | "rejected"
  | "suspended"
  | "contribution_required"
  | "admin"
  | "active";

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
  chat_entitled?: boolean;
}

export interface ChatEntitlementResponse {
  eligible: boolean;
  reason_code: string;
  eligible_credential_count: number;
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
export type BillingTier = "verified_free_only" | "paid" | "unknown";

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
  account_scope_verified?: boolean;
  account_scope_source?: string | null;
  account_scope_verified_at?: string | null;
  billing_tier?: BillingTier;
  billing_verified_at?: string | null;
  billing_verification_error?: string | null;
  daily_request_limit: number;
  monthly_request_limit: number;
  daily_spending_limit_cents?: number | null;
  monthly_spending_limit_cents?: number | null;
  created_at: string;
  updated_at: string;
}

export interface CredentialListResponse {
  items: ApiKeyCredential[];
  credentials?: ApiKeyCredential[];
  total: number;
}

export interface CredentialCreateInput {
  provider_id: string;
  nickname: string;
  api_key: string;
  sharing_scope?: CredentialSharingScope;
  shared_consent?: boolean;
  daily_request_limit?: number;
  monthly_request_limit?: number;
  daily_spending_limit_cents?: number | null;
  monthly_spending_limit_cents?: number | null;
}

export interface CredentialUpdateInput {
  nickname?: string;
  sharing_scope?: CredentialSharingScope;
  shared_consent?: boolean;
  daily_request_limit?: number;
  monthly_request_limit?: number;
  daily_spending_limit_cents?: number | null;
  monthly_spending_limit_cents?: number | null;
}

export interface ValidationResult {
  is_valid: boolean;
  outcome: string;
  http_status?: number | null;
  error_code?: string | null;
  safe_code?: string;
  message?: string;
  diagnostic_message?: string;
  billing_tier?: BillingTier;
  billing_verification_error?: string | null;
  probed_at?: string;
  validated_at?: string;
}

export interface ReservationView {
  id: string;
  request_id: string;
  provider_id: string;
  model_id: string;
  status:
  | "reserved"
  | "dispatched"
  | "settled"
  | "released"
  | "outcome_unknown"
  | "reconciliation_required"
  | "expired";
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  estimated_cost_cents: number;
  estimated_cost_microcents?: number;
  actual_input_tokens?: number | null;
  actual_output_tokens?: number | null;
  actual_cost_cents?: number | null;
  actual_cost_microcents?: number | null;
  currency: string;
  created_at: string;
  dispatched_at?: string | null;
  settled_at?: string | null;
}

export interface UsageSummaryResponse {
  total_dispatched_requests: number;
  total_settled_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_actual_cost_cents: number;
  total_actual_cost_microcents?: number;
  total_reserved_cost_cents: number;
  daily_request_usage: number;
  daily_request_limit: number;
  monthly_request_usage: number;
  monthly_request_limit: number;
  daily_spending_cents: number;
  daily_spending_microcents?: number;
  daily_spending_budget_cents?: number | null;
  monthly_spending_cents: number;
  monthly_spending_microcents?: number;
  monthly_spending_budget_cents?: number | null;
  unknown_outcome_count: number;
  recent_reservations: ReservationView[];
}

// =============================================================================
// Milestone M2.9: Intelligent Routing
// =============================================================================
export type RoutingMode = "auto" | "cloud_only" | "community_only" | "provider_specific";

export interface RoutePreviewCandidate {
  source_type: "cloud" | "community";
  provider_id: string;
  model_id: string;
  display_name: string;
  family: string;
  free_tier_eligible: boolean;
  pricing_input_cents_per_million?: number | null;
  pricing_output_cents_per_million?: number | null;
}

export interface RoutePreviewResponse {
  routing_mode: RoutingMode;
  selected_candidate?: RoutePreviewCandidate | null;
  candidates_considered: number;
  candidates_eligible: number;
  decision_reason: string;
  rejected_reasons_summary: Record<string, number>;
}
