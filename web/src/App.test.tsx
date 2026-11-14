import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "./api";
import { App } from "./App";
import { makeClaimSet, makeSnapshot } from "./test/fixtures";
import type { ClaimsResponse, PullRequestSnapshot } from "./types";

const LINK = "https://github.com/octo-org/widgets/pull/42";
const never = () => new Promise<ClaimsResponse>(() => undefined);

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
    render(<App loadSnapshot={loadSnapshot} loadClaims={never} />);

    await user.tab();
    expect(screen.getByLabelText("Pull request link")).toHaveFocus();
    await user.keyboard(`${LINK}{Enter}`);

    expect(
      await screen.findByRole("heading", { name: "Add retry to the upload client" }),
    ).toBeInTheDocument();
    expect(loadSnapshot).toHaveBeenCalledWith(LINK);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Loaded octo-org/widgets #42. Finding its claims…",
    );
  });

  it("checks the link shape before calling the runner", async () => {
    const user = userEvent.setup();
    const loadSnapshot = vi.fn();
    render(<App loadSnapshot={loadSnapshot} loadClaims={never} />);

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
    render(<App loadSnapshot={() => pending.promise} loadClaims={never} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Loading the pull request");
    pending.resolve(makeSnapshot());
    expect(await screen.findByRole("region", { name: "Claims" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();
  });

  it("shows the runner's plain-language error as an alert", async () => {
    const user = userEvent.setup();
    const loadSnapshot = () =>
      Promise.reject(new ApiError("Couldn't find octo-org/widgets#42.", "github_not_found"));
    render(<App loadSnapshot={loadSnapshot} loadClaims={never} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't find octo-org/widgets#42.",
    );
  });

  it("never shows raw exception text", async () => {
    const user = userEvent.setup();
    const loadSnapshot = () => Promise.reject(new Error("TypeError at line 12: secret internals"));
    render(<App loadSnapshot={loadSnapshot} loadClaims={never} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Try again.");
    expect(alert).not.toHaveTextContent("secret internals");
  });

  it("shows the snapshot first, then the claims for that same snapshot", async () => {
    const user = userEvent.setup();
    const claims = deferred<ClaimsResponse>();
    const loadClaims = vi.fn(() => claims.promise);
    render(<App loadSnapshot={() => Promise.resolve(makeSnapshot())} loadClaims={loadClaims} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);
    expect(
      await screen.findByRole("heading", { name: "Add retry to the upload client" }),
    ).toBeInTheDocument();
    expect(loadClaims).toHaveBeenCalledWith(LINK);

    claims.resolve({ snapshot: makeSnapshot({ title: "Retry uploads" }), claims: makeClaimSet() });

    expect(await screen.findByRole("heading", { name: "Claims (2)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Retry uploads" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Found 2 claims.");
    expect(screen.getByRole("button", { name: "Load pull request" })).toBeEnabled();
  });

  it("keeps the snapshot when finding claims fails", async () => {
    const user = userEvent.setup();
    const loadClaims = () =>
      Promise.reject(new ApiError("Claude Code isn't signed in.", "llm_unavailable"));
    render(<App loadSnapshot={() => Promise.resolve(makeSnapshot())} loadClaims={loadClaims} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't pull out the claims. Claude Code isn't signed in.",
    );
    expect(
      screen.getByRole("heading", { name: "Add retry to the upload client" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Its claims couldn't be found.");
    expect(screen.getByRole("button", { name: "Load pull request" })).toBeEnabled();
  });

  it("doesn't ask for claims when the snapshot fails", async () => {
    const user = userEvent.setup();
    const loadClaims = vi.fn(never);
    const loadSnapshot = () => Promise.reject(new ApiError("Not found.", "github_not_found"));
    render(<App loadSnapshot={loadSnapshot} loadClaims={loadClaims} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
    expect(loadClaims).not.toHaveBeenCalled();
  });
});
