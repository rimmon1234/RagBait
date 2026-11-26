import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import CommunityNodesPage from "@/app/(protected)/community-nodes/page";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: replaceMock,
  }),
}));

describe("CommunityNodesPage", () => {
  it("renders redirect notice and redirects to /chat", () => {
    render(<CommunityNodesPage />);
    expect(screen.getByText("Redirecting to AI Chat...")).toBeInTheDocument();
    expect(replaceMock).toHaveBeenCalledWith("/chat");
  });
});
