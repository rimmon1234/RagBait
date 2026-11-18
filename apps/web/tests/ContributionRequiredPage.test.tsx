import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ContributionRequiredPage from "@/app/(protected)/access/contribution-required/page";

describe("ContributionRequiredPage", () => {
  it("renders approved student message with M2 feature gate notice", () => {
    render(<ContributionRequiredPage />);
    expect(screen.getByText("Account Approved!")).toBeInTheDocument();
    expect(screen.getByText("Milestone M2 Feature Gate")).toBeInTheDocument();
  });
});
