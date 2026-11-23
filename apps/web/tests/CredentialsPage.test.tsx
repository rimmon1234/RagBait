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
    id: "groq",
    name: "Groq",
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
    id: "gemini",
    name: "Google Gemini",
    runtime_type: "cloud_api",
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
    provider_id: "groq",
    nickname: "Research Groq Key",
    masked_key: "gsk_...4928",
    status: "active",
    sharing_scope: "community_shared",
    shared_consent: true,
    sharing_consent_timestamp: "2026-02-01T00:00:00Z",
    consent_version: "v1",
    validation_status: "valid",
    last_validated_at: "2026-02-01T12:00:00Z",
    daily_request_limit: 1000,
    monthly_request_limit: 25000,
    daily_spending_limit_cents: 200,
    monthly_spending_limit_cents: 5000,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
  },
];

describe("CredentialsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders empty state when no credentials exist", async () => {
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
    expect(screen.getByText(/Add your first AI provider API credential/i)).toBeInTheDocument();
  });

  it("renders credentials list with masked key and limits (never plaintext)", async () => {
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
      expect(screen.getByText("Research Groq Key")).toBeInTheDocument();
    });

    // Check masked key
    expect(screen.getByText("gsk_...4928")).toBeInTheDocument();
    // Verify plaintext is not rendered
    expect(screen.queryByText(/gsk_raw_secret/i)).not.toBeInTheDocument();

    // Check badges
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Valid")).toBeInTheDocument();
    expect(screen.getByText("Community Shared")).toBeInTheDocument();
    expect(screen.getByText("500 RPD Baseline")).toBeInTheDocument();
  });

  it("opens add credential modal and validates required consent", async () => {
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

    // Fill in nickname and key
    const nicknameInput = screen.getByPlaceholderText(/e\.g\. My/i);
    const keyInput = screen.getByPlaceholderText(/gsk_/i);

    fireEvent.change(nicknameInput, { target: { value: "My Shared Key" } });
    fireEvent.change(keyInput, { target: { value: "gsk_test_secret_key_123" } });

    // Enable community sharing consent checkbox
    const consentCheckbox = screen.getByRole("checkbox");
    fireEvent.click(consentCheckbox);

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
          body: expect.stringContaining('"nickname":"My Shared Key"'),
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
      expect(screen.getByText("Research Groq Key")).toBeInTheDocument();
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
      expect(screen.getByText("Research Groq Key")).toBeInTheDocument();
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
