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
  it("renders redirect notice and redirects to /local-inference", () => {
    render(<CommunityNodesPage />);
    expect(screen.getByText("Redirecting to Local Inference...")).toBeInTheDocument();
    expect(replaceMock).toHaveBeenCalledWith("/local-inference");
  });
});
