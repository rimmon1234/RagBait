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
