import { render, screen, waitFor } from "@testing-library/react";
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Navbar } from "@/components/Navbar";
import { middleware } from "@/middleware";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: {
      getSession: vi.fn(async () => ({
        data: {
          session: {
            access_token: "test-token",
            user: {
              id: "user-1",
              email: "student@univ.edu",
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-01T00:00:00Z",
              user_metadata: { display_name: "Test Student" },
              app_metadata: { role: "user" },
            },
          },
        },
      })),
      signOut: vi.fn(),
      onAuthStateChange: () => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      }),
    },
  }),
}));

vi.mock("@/lib/supabase/middleware", () => ({
  updateSession: vi.fn(async () => new Response("next", { status: 200 })),
}));

describe("Unified AI Chat Navigation & Legacy Route Redirects", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            id: "user-1",
            email: "student@univ.edu",
            display_name: "Test Student",
            role: "user",
            approval_status: "approved",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );
  });

  it("exposes a single canonical AI Chat destination in Navbar and removes AI Inference", async () => {
    render(<Navbar />);

    await waitFor(() => {
      const chatLink = screen.getByRole("link", { name: /AI Chat/i });
      expect(chatLink).toHaveAttribute("href", "/chat");
    });

    expect(screen.queryByRole("link", { name: /AI Inference/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Study Chat/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Knowledge Base/i })).toHaveAttribute(
      "href",
      "/knowledge-base"
    );
    expect(screen.getByRole("link", { name: /Credentials/i })).toHaveAttribute(
      "href",
      "/credentials"
    );
  });

  it("redirects legacy /local-inference and /community-nodes paths to /chat in middleware", async () => {
    for (const path of [
      "/local-inference",
      "/local-inference/setup",
      "/community-nodes",
      "/community-nodes/old",
    ]) {
      const req = new NextRequest(`http://localhost:3000${path}`);
      const res = await middleware(req);
      expect(res.status).toBeGreaterThanOrEqual(300);
      expect(res.status).toBeLessThan(400);
      expect(res.headers.get("location")).toBe("http://localhost:3000/chat");
    }

    const normalRes = await middleware(new NextRequest("http://localhost:3000/chat"));
    expect(normalRes.status).toBe(200);
  });
});
