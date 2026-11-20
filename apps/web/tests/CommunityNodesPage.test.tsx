import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CommunityNodesPage from "@/app/(protected)/community-nodes/page";
import * as communityClient from "@/lib/community-client";
import type { CommunityNode } from "@/types/community";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: {
      getSession: vi.fn().mockResolvedValue({
        data: { session: { access_token: "mock-token" } },
      }),
    },
  }),
}));

vi.mock("@/lib/community-client", () => ({
  listOwnedNodes: vi.fn(),
  createPairingCode: vi.fn(),
  updateNodeConfig: vi.fn(),
  updateNodeModels: vi.fn(),
  pauseNode: vi.fn(),
  resumeNode: vi.fn(),
  revokeNode: vi.fn(),
}));

const mockNode: CommunityNode = {
  id: "node-1111-2222",
  owner_id: "user-1111",
  display_name: "Desktop Workstation",
  public_key: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  key_version: 1,
  agent_version: "0.1.0",
  os: "Windows",
  architecture: "AMD64",
  ollama_version: "0.40.16",
  status: "active",
  sharing_enabled: true,
  max_concurrent_jobs: 1,
  max_execution_duration: 120,
  last_seen_at: "2026-02-10T00:00:00Z",
  created_at: "2026-02-09T00:00:00Z",
  models: [
    {
      id: "model-1",
      node_id: "node-1111-2222",
      model_name: "llama3.2:3b",
      enabled: true,
      parameter_size: "3B",
      quantization_level: "Q4_K_M",
      size_bytes: 2000000000,
      last_seen_at: "2026-02-10T00:00:00Z",
    },
  ],
};

describe("CommunityNodesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders empty state when user has no nodes", async () => {
    vi.mocked(communityClient.listOwnedNodes).mockResolvedValue([]);

    render(<CommunityNodesPage />);

    expect(screen.getByText("Community Inference Nodes")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("No community nodes paired")).toBeInTheDocument();
    });
  });

  it("renders paired node card with model allowlist", async () => {
    vi.mocked(communityClient.listOwnedNodes).mockResolvedValue([mockNode]);

    render(<CommunityNodesPage />);

    await waitFor(() => {
      expect(screen.getByText("Desktop Workstation")).toBeInTheDocument();
    });
    expect(screen.getByText("llama3.2:3b")).toBeInTheDocument();
    expect(screen.getByText(/3B/)).toBeInTheDocument();
  });

  it("opens pairing code modal when Pair New Node is clicked", async () => {
    vi.mocked(communityClient.listOwnedNodes).mockResolvedValue([]);
    vi.mocked(communityClient.createPairingCode).mockResolvedValue({
      pairing_code: "RB-7A9B1C",
      expires_at: "2026-02-10T00:05:00Z",
      ttl_seconds: 300,
    });

    render(<CommunityNodesPage />);

    const pairBtn = await screen.findByRole("button", { name: /pair new node/i });
    fireEvent.click(pairBtn);

    await waitFor(() => {
      expect(screen.getByText("RB-7A9B1C")).toBeInTheDocument();
    });
    expect(screen.getByText(/ragbait-node pair/i)).toBeInTheDocument();
  });

  it("allows toggling node pause status", async () => {
    vi.mocked(communityClient.listOwnedNodes).mockResolvedValue([mockNode]);
    vi.mocked(communityClient.pauseNode).mockResolvedValue();

    render(<CommunityNodesPage />);

    const pauseBtn = await screen.findByRole("button", { name: /pause/i });
    fireEvent.click(pauseBtn);

    await waitFor(() => {
      expect(communityClient.pauseNode).toHaveBeenCalledWith("node-1111-2222");
    });
  });
});
