import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CommunityConsentModal } from "@/components/CommunityConsentModal";

describe("CommunityConsentModal", () => {
  it("does not render when isOpen is false", () => {
    const { container } = render(
      <CommunityConsentModal
        isOpen={false}
        modelName="llama3.2:3b"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders disclosure and model name when open", () => {
    render(
      <CommunityConsentModal
        isOpen={true}
        modelName="llama3.2:3b"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );

    expect(screen.getByText("Community Inference Privacy Disclosure")).toBeInTheDocument();
    expect(screen.getByText("llama3.2:3b")).toBeInTheDocument();
    expect(
      screen.getByText(/ephemeral in-flight delivery/i)
    ).toBeInTheDocument();
  });

  it("disables confirm button until checkbox is checked", () => {
    const onConfirm = vi.fn();
    render(
      <CommunityConsentModal
        isOpen={true}
        modelName="llama3.2:3b"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    );

    const confirmBtn = screen.getByRole("button", { name: /agree & continue/i });
    expect(confirmBtn).toBeDisabled();

    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);

    expect(confirmBtn).not.toBeDisabled();
    fireEvent.click(confirmBtn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when Cancel button is clicked", () => {
    const onCancel = vi.fn();
    render(
      <CommunityConsentModal
        isOpen={true}
        modelName="llama3.2:3b"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />
    );

    const cancelBtn = screen.getByRole("button", { name: /cancel/i });
    fireEvent.click(cancelBtn);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
