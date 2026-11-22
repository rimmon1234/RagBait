import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ContributionRequiredPage from "@/app/(protected)/access/contribution-required/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
  }),
}));

describe("ContributionRequiredPage", () => {
  it("renders approved student message with M2 feature gate notice", () => {
    render(<ContributionRequiredPage />);
    expect(screen.getByText("Account Approved!")).toBeInTheDocument();
    expect(screen.getByText("Milestone M2 Feature Gate")).toBeInTheDocument();
  });
});
