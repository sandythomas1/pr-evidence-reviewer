import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "./api";
import { App } from "./App";
import { makeSnapshot } from "./test/fixtures";
import type { PullRequestSnapshot } from "./types";

const LINK = "https://github.com/octo-org/widgets/pull/42";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("App", () => {
  it("loads a pull request using only the keyboard", async () => {
    const user = userEvent.setup();
    const loadSnapshot = vi.fn(() => Promise.resolve(makeSnapshot()));
    render(<App loadSnapshot={loadSnapshot} />);

    await user.tab();
    expect(screen.getByLabelText("Pull request link")).toHaveFocus();
    await user.keyboard(`${LINK}{Enter}`);

    expect(
      await screen.findByRole("heading", { name: "Add retry to the upload client" }),
    ).toBeInTheDocument();
    expect(loadSnapshot).toHaveBeenCalledWith(LINK);
    expect(screen.getByRole("status")).toHaveTextContent("Loaded octo-org/widgets #42.");
  });

  it("checks the link shape before calling the runner", async () => {
    const user = userEvent.setup();
    const loadSnapshot = vi.fn();
    render(<App loadSnapshot={loadSnapshot} />);

    await user.type(screen.getByLabelText("Pull request link"), "https://github.com/octo-org");
    await user.click(screen.getByRole("button", { name: "Load pull request" }));

    const input = screen.getByLabelText("Pull request link");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/doesn't look like a GitHub pull request link/);
    expect(loadSnapshot).not.toHaveBeenCalled();
  });

  it("announces loading and disables the button while waiting", async () => {
    const user = userEvent.setup();
    const pending = deferred<PullRequestSnapshot>();
    render(<App loadSnapshot={() => pending.promise} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Loading the pull request");
    pending.resolve(makeSnapshot());
    expect(await screen.findByRole("button", { name: "Load pull request" })).toBeEnabled();
  });

  it("shows the runner's plain-language error as an alert", async () => {
    const user = userEvent.setup();
    const loadSnapshot = () =>
      Promise.reject(new ApiError("Couldn't find octo-org/widgets#42.", "github_not_found"));
    render(<App loadSnapshot={loadSnapshot} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't find octo-org/widgets#42.",
    );
  });

  it("never shows raw exception text", async () => {
    const user = userEvent.setup();
    const loadSnapshot = () => Promise.reject(new Error("TypeError at line 12: secret internals"));
    render(<App loadSnapshot={loadSnapshot} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Try again.");
    expect(alert).not.toHaveTextContent("secret internals");
  });
});
