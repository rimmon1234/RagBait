import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import UsageLedgerPage from "@/app/(protected)/credentials/usage/page";
import * as api from "@/lib/api";
import type { UsageSummaryResponse } from "@/types";

vi.mock("@/lib/api", () => ({
  fetchWithAuth: vi.fn(),
  ApiError: class extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
      this.name = "ApiError";
    }
  },
}));

const mockSummary: UsageSummaryResponse = {
  total_dispatched_requests: 15,
  total_settled_requests: 14,
  total_input_tokens: 12000,
  total_output_tokens: 3500,
  total_actual_cost_cents: 250,
  total_reserved_cost_cents: 20,
  daily_request_usage: 5,
  daily_request_limit: 500,
  monthly_request_usage: 15,
  monthly_request_limit: 15000,
  daily_spending_cents: 80,
  daily_spending_budget_cents: 500,
  monthly_spending_cents: 250,
  monthly_spending_budget_cents: 10000,
  unknown_outcome_count: 1,
  recent_reservations: [
    {
      id: "99999999-9999-9999-9999-999999999999",
      request_id: "req_correl_test_12345",
      provider_id: "groq",
      model_id: "openai/gpt-oss-120b",
      status: "settled",
      estimated_input_tokens: 500,
      estimated_output_tokens: 200,
      estimated_cost_cents: 20,
      actual_input_tokens: 480,
      actual_output_tokens: 190,
      actual_cost_cents: 18,
      currency: "USD",
      created_at: "2026-02-04T12:00:00Z",
      dispatched_at: "2026-02-04T12:00:01Z",
      settled_at: "2026-02-04T12:00:05Z",
    },
  ],
};

describe("UsageLedgerPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders usage metrics, reservations, and ambiguous outcome notice", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValueOnce(mockSummary);

    render(<UsageLedgerPage />);

    expect(screen.getByText("Usage Ledger & Quota Consumption")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("5")).toBeInTheDocument();
      expect(screen.getByText("/ 500")).toBeInTheDocument();
      expect(screen.getAllByText("15").length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("/ 15000")).toBeInTheDocument();
      expect(screen.getByText("$0.80")).toBeInTheDocument();
      expect(screen.getByText("/ $5.00")).toBeInTheDocument();
      expect(screen.getAllByText("$2.50").length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("/ $100.00")).toBeInTheDocument();
    });

    // Check ambiguous outcome warning
    expect(screen.getByText(/Ambiguous Operations Detected/i)).toBeInTheDocument();

    // Check recent reservations
    expect(screen.getByText("req_correl_test_12345")).toBeInTheDocument();
    expect(screen.getByText(/openai\/gpt-oss-120b/i)).toBeInTheDocument();
    expect(screen.getByText("Settled")).toBeInTheDocument();
    expect(screen.getByText("670 tokens")).toBeInTheDocument();
  });

  it("refreshes usage data when refresh button is clicked", async () => {
    vi.mocked(api.fetchWithAuth).mockResolvedValue(mockSummary);

    render(<UsageLedgerPage />);

    await waitFor(() => {
      expect(screen.getByText("req_correl_test_12345")).toBeInTheDocument();
    });

    const refreshBtn = screen.getByRole("button", { name: /Refresh/i });
    fireEvent.click(refreshBtn);

    await waitFor(() => {
      expect(api.fetchWithAuth).toHaveBeenCalledTimes(2);
    });
  });
});
