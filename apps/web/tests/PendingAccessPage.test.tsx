import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PendingAccessPage from "@/app/(protected)/access/pending/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

describe("PendingAccessPage", () => {
  it("renders pending approval notification and refresh button", () => {
    render(<PendingAccessPage />);
    expect(screen.getByText("Access Request Pending")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Your email has been confirmed and your access request has been submitted to the platform administrators."
      )
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Refresh Approval Status/i })
    ).toBeInTheDocument();
  });
});
