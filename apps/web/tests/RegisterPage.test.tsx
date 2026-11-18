import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import RegisterPage from "@/app/(auth)/register/page";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
}));

const mockSignUp = vi.fn();
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: {
      signUp: mockSignUp,
    },
  }),
}));

describe("RegisterPage", () => {
  it("renders registration form fields", () => {
    render(<RegisterPage />);
    expect(screen.getByText("Join RagBait")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Jane Doe")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("jane.doe@university.edu")).toBeInTheDocument();
  });

  it("validates short password", async () => {
    render(<RegisterPage />);

    fireEvent.change(screen.getByPlaceholderText("Jane Doe"), {
      target: { value: "Jane Doe" },
    });
    fireEvent.change(screen.getByPlaceholderText("jane.doe@university.edu"), {
      target: { value: "jane@test.edu" },
    });
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "123" },
    });

    fireEvent.click(screen.getByRole("button", { name: /Register/i }));

    await waitFor(() => {
      expect(
        screen.getByText("Password must be at least 8 characters long.")
      ).toBeInTheDocument();
    });
  });

  it("submits valid registration and redirects to verify", async () => {
    mockSignUp.mockResolvedValueOnce({ data: { user: {} }, error: null });

    render(<RegisterPage />);

    fireEvent.change(screen.getByPlaceholderText("Jane Doe"), {
      target: { value: "Jane Doe" },
    });
    fireEvent.change(screen.getByPlaceholderText("jane.doe@university.edu"), {
      target: { value: "jane@test.edu" },
    });
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "securepassword123" },
    });

    fireEvent.click(screen.getByRole("button", { name: /Register/i }));

    await waitFor(() => {
      expect(mockSignUp).toHaveBeenCalledWith({
        email: "jane@test.edu",
        password: "securepassword123",
        options: {
          data: {
            full_name: "Jane Doe",
          },
        },
      });
      expect(mockPush).toHaveBeenCalledWith("/auth/verify?email=jane%40test.edu");
    });
  });
});
