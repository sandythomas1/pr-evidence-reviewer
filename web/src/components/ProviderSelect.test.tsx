import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeProviders } from "../test/fixtures";
import { defaultProvider, ProviderSelect } from "./ProviderSelect";

function renderLoaded(selected: string | null, onChange = vi.fn()) {
  render(
    <ProviderSelect
      state={{ kind: "loaded", providers: makeProviders() }}
      selected={selected}
      disabled={false}
      onChange={onChange}
    />,
  );
  return onChange;
}

describe("ProviderSelect", () => {
  it("defaults to the first ready provider", () => {
    const providers = makeProviders();

    expect(defaultProvider(providers)).toBe("claude_code");
    expect(defaultProvider(providers.map((p) => ({ ...p, available: false })))).toBeNull();
  });

  it("says in words, not only by style, which providers aren't ready and why", () => {
    renderLoaded("claude_code");

    expect(screen.getByRole("option", { name: "Codex (not ready)" })).toBeDisabled();
    expect(screen.getByText("Codex:")).toBeVisible();
    expect(screen.getByText(/Codex isn't installed/)).toBeVisible();
  });

  it("changes the choice from the keyboard", async () => {
    const user = userEvent.setup();
    const onChange = renderLoaded("claude_code");

    await user.tab();
    expect(screen.getByLabelText("Model provider")).toHaveFocus();
    await user.selectOptions(screen.getByLabelText("Model provider"), "gemini");

    expect(onChange).toHaveBeenCalledWith("gemini");
  });

  it("shows the chosen provider's own note, such as the free-account warning", () => {
    renderLoaded("gemini");

    expect(screen.getByLabelText("Model provider")).toHaveAccessibleDescription(
      /With a free Google account, Google may use what you send/,
    );
  });

  it("says so when no provider is ready", () => {
    const providers = makeProviders().map((p) => ({ ...p, available: false }));
    render(
      <ProviderSelect
        state={{ kind: "loaded", providers }}
        selected={null}
        disabled={false}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Model provider")).toHaveValue("");
    expect(screen.getByRole("option", { name: "No model provider is ready" })).toBeDisabled();
  });

  it("shows progress while the list loads", () => {
    render(
      <ProviderSelect
        state={{ kind: "loading" }}
        selected={null}
        disabled={false}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Checking which model providers are ready…")).toBeInTheDocument();
  });
});
