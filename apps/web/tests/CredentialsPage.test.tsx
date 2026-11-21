import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CredentialsPage from "@/app/(protected)/credentials/page";
import * as api from "@/lib/api";
import type { ApiKeyCredential, LLMProvider, ValidationResult } from "@/types";

vi.mock("@/lib/api", () => ({
  fetchWithAuth: vi.fn(),
  ApiError: class extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
      this.name = "ApiError";
    }
  },
}));

const mockProviders: LLMProvider[] = [
  {
    id: "openrouter",
    name: "OpenRouter",
    runtime_type: "cloud_api",
    is_enabled: true,
    requires_account_scope: false,
    allows_community_sharing: true,
    account_tier: "free",
    billing_status: "active",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "groq",
    name: "Groq",
    runtime_type: "cloud_api",
    is_enabled: true,
    requires_account_scope: false,
    allows_community_sharing: false,
    account_tier: "free",
    billing_status: "active",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "ollama",
    name: "Ollama",
    runtime_type: "local_runtime",
    is_enabled: true,
    requires_account_scope: false,
    allows_community_sharing: false,
    account_tier: "standard",
    billing_status: "active",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

const mockCredentials: ApiKeyCredential[] = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    provider_id: "openrouter",
    nickname: "Research OpenRouter Key",
    masked_key: "sk-or-v1-...4928",
    status: "active",
    sharing_scope: "community_shared",
    shared_consent: true,
    sharing_consent_timestamp: "2026-02-01T00:00:00Z",
    consent_version: "v1",
    validation_status: "valid",
    last_validated_at: "2026-02-01T12:00:00Z",
    billing_tier: "verified_free_only",
    daily_request_limit: 500,
    monthly_request_limit: 15000,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
  },
];

describe("CredentialsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders empty state when no credentials exist and filters out local runtime (Ollama)", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/me/api-keys")) {
        return { credentials: [], total: 0 };
      }
      if (url.includes("/api/v1/llm/providers")) {
        return mockProviders;
      }
      throw new Error(`Unhandled url ${url}`);
    });

    render(<CredentialsPage />);

    expect(screen.getByText("API Credentials Vault")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("No credentials configured")).toBeInTheDocument();
    });

    // Open add modal
    const addButtons = screen.getAllByRole("button", { name: /Add Credential/i });
    fireEvent.click(addButtons[0]);

    // Verify Ollama does not appear in options
    expect(screen.queryByRole("option", { name: /Ollama/i })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /OpenRouter/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Groq/i })).toBeInTheDocument();
  });

  it("renders credentials list with masked key and platform quotas (never plaintext)", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/me/api-keys")) {
        return { credentials: mockCredentials, total: 1 };
      }
      if (url.includes("/api/v1/llm/providers")) {
        return mockProviders;
      }
      throw new Error(`Unhandled url ${url}`);
    });

    render(<CredentialsPage />);

    await waitFor(() => {
      expect(screen.getByText("Research OpenRouter Key")).toBeInTheDocument();
    });

    // Check masked key
    expect(screen.getByText("sk-or-v1-...4928")).toBeInTheDocument();

    // Check badges
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Valid")).toBeInTheDocument();
    expect(screen.getByText("Community Shared")).toBeInTheDocument();
    expect(screen.getByText("500 RPD Baseline")).toBeInTheDocument();
    expect(screen.getByText("Redis Admission Engine")).toBeInTheDocument();
  });

  it("opens add modal with simplified inputs and allows opting into community sharing for approved providers", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/me/api-keys")) {
        return { credentials: [], total: 0 };
      }
      if (url.includes("/api/v1/llm/providers")) {
        return mockProviders;
      }
      throw new Error(`Unhandled url ${url}`);
    });

    render(<CredentialsPage />);

    await waitFor(() => {
      expect(screen.getByText("No credentials configured")).toBeInTheDocument();
    });

    // Click Add Credential
    const addButtons = screen.getAllByRole("button", { name: /Add Credential/i });
    fireEvent.click(addButtons[0]);

    expect(screen.getByText("Add AI Provider Credential")).toBeInTheDocument();

    // Verify user limit and spending cap inputs do NOT exist
    expect(screen.queryByLabelText(/Daily Request Limit/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Monthly Request Limit/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Daily Spending Cap/i)).not.toBeInTheDocument();

    // Fill in nickname and key
    const nicknameInput = screen.getByPlaceholderText(/e\.g\. My OpenRouter Free Key/i);
    const keyInput = screen.getByPlaceholderText(/sk-or-\.\.\./i);

    fireEvent.change(nicknameInput, { target: { value: "My Shared Key" } });
    fireEvent.change(keyInput, { target: { value: "sk-or-v1-test-secret-key-123" } });

    // Explicit consent checkbox should be unchecked by default
    const consentCheckbox = screen.getByRole("checkbox");
    expect(consentCheckbox).not.toBeChecked();

    // Check consent checkbox
    fireEvent.click(consentCheckbox);
    expect(consentCheckbox).toBeChecked();

    // Mock successful creation
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce({
      ...mockCredentials[0],
      nickname: "My Shared Key",
    });

    const submitButton = screen.getByRole("button", { name: /Encrypt & Save Key/i });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(api.fetchWithAuth).toHaveBeenCalledWith(
        "/api/v1/me/api-keys",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            provider_id: "openrouter",
            nickname: "My Shared Key",
            api_key: "sk-or-v1-test-secret-key-123",
            sharing_scope: "community_shared",
            shared_consent: true,
          }),
        })
      );
    });
  });

  it("handles quick privacy toggle: Make Private immediately revokes consent", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.includes(`/api/v1/me/api-keys/${mockCredentials[0].id}`) && options?.method === "PATCH") {
        return {
          ...mockCredentials[0],
          sharing_scope: "owner_only",
          shared_consent: false,
        };
      }
      if (url.includes("/api/v1/me/api-keys")) {
        return { credentials: mockCredentials, total: 1 };
      }
      if (url.includes("/api/v1/llm/providers")) {
        return mockProviders;
      }
      throw new Error(`Unhandled url ${url}`);
    });

    render(<CredentialsPage />);

    await waitFor(() => {
      expect(screen.getByText("Research OpenRouter Key")).toBeInTheDocument();
    });

    const makePrivateBtn = screen.getByRole("button", { name: /Make Private/i });
    fireEvent.click(makePrivateBtn);

    await waitFor(() => {
      expect(api.fetchWithAuth).toHaveBeenCalledWith(
        `/api/v1/me/api-keys/${mockCredentials[0].id}`,
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({
            sharing_scope: "owner_only",
            shared_consent: false,
          }),
        })
      );
    });
  });

  it("triggers non-generative validation probe on credential", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.includes("/validate") && options?.method === "POST") {
        const valRes: ValidationResult = {
          is_valid: true,
          outcome: "valid",
          http_status: 200,
          diagnostic_message: "Authentication successful (0 inference tokens consumed)",
          probed_at: new Date().toISOString(),
        };
        return valRes;
      }
      if (url.includes("/api/v1/me/api-keys")) {
        return { credentials: mockCredentials, total: 1 };
      }
      if (url.includes("/api/v1/llm/providers")) {
        return mockProviders;
      }
      throw new Error(`Unhandled url ${url}`);
    });

    render(<CredentialsPage />);

    await waitFor(() => {
      expect(screen.getByText("Research OpenRouter Key")).toBeInTheDocument();
    });

    const validateBtn = screen.getByRole("button", { name: /Validate/i });
    fireEvent.click(validateBtn);

    await waitFor(() => {
      expect(api.fetchWithAuth).toHaveBeenCalledWith(
        `/api/v1/me/api-keys/${mockCredentials[0].id}/validate`,
        expect.objectContaining({ method: "POST" })
      );
      expect(
        screen.getByText("Authentication successful (0 inference tokens consumed)")
      ).toBeInTheDocument();
    });
  });

  it("opens delete confirmation modal with cryptographic purge explanation", async () => {
    vi.mocked(api.fetchWithAuth).mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.includes(`/api/v1/me/api-keys/${mockCredentials[0].id}`) && options?.method === "DELETE") {
        return {};
      }
      if (url.includes("/api/v1/me/api-keys")) {
        return { credentials: mockCredentials, total: 1 };
      }
      if (url.includes("/api/v1/llm/providers")) {
        return mockProviders;
      }
      throw new Error(`Unhandled url ${url}`);
    });

    render(<CredentialsPage />);

    await waitFor(() => {
      expect(screen.getByText("Research OpenRouter Key")).toBeInTheDocument();
    });

    const deleteBtn = screen.getByTitle("Delete credential");
    fireEvent.click(deleteBtn);

    // Delete dialog appears
    expect(screen.getByText("Delete API Credential?")).toBeInTheDocument();
    expect(screen.getByText("Cryptographic Purge Notice:")).toBeInTheDocument();

    const confirmBtn = screen.getByRole("button", { name: /Confirm Deletion/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.fetchWithAuth).toHaveBeenCalledWith(
        `/api/v1/me/api-keys/${mockCredentials[0].id}`,
        expect.objectContaining({ method: "DELETE" })
      );
    });
  });
});
