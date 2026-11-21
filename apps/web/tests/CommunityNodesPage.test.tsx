import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import CommunityNodesPage from "@/app/(protected)/community-nodes/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

describe("CommunityNodesPage", () => {
  it("renders discontinued status and official privacy statement", () => {
    render(<CommunityNodesPage />);

    expect(screen.getByText("Community Compute Discontinued")).toBeInTheDocument();
    expect(
      screen.getByText("Official Privacy Policy: Private Local Inference Only")
    ).toBeInTheDocument();
    expect(
      screen.getByText(/remain exclusively available to that student/i)
    ).toBeInTheDocument();
  });

  it("renders navigation links to private local inference and API credentials", () => {
    render(<CommunityNodesPage />);

    const localLink = screen.getByRole("link", { name: /go to local inference/i });
    expect(localLink).toHaveAttribute("href", "/local-inference");

    const credLink = screen.getByRole("link", { name: /manage api credentials/i });
    expect(credLink).toHaveAttribute("href", "/credentials");
  });
});
