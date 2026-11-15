import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "./api";
import { App } from "./App";
import { makeClaimSet, makeProviders, makeSnapshot, makeVerdictSet } from "./test/fixtures";
import type { ClaimsResponse, PullRequestSnapshot, VerdictsResponse } from "./types";

const LINK = "https://github.com/octo-org/widgets/pull/42";
const never = () => new Promise<ClaimsResponse>(() => undefined);
const neverChecked = () => new Promise<VerdictsResponse>(() => undefined);
const providers = () => Promise.resolve(makeProviders());

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
    render(<App loadProviders={providers} loadSnapshot={loadSnapshot} loadClaims={never} />);

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
    render(<App loadProviders={providers} loadSnapshot={loadSnapshot} loadClaims={never} />);

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
    render(
      <App loadProviders={providers} loadSnapshot={() => pending.promise} loadClaims={never} />,
    );

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
    render(<App loadProviders={providers} loadSnapshot={loadSnapshot} loadClaims={never} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't find octo-org/widgets#42.",
    );
  });

  it("never shows raw exception text", async () => {
    const user = userEvent.setup();
    const loadSnapshot = () => Promise.reject(new Error("TypeError at line 12: secret internals"));
    render(<App loadProviders={providers} loadSnapshot={loadSnapshot} loadClaims={never} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Try again.");
    expect(alert).not.toHaveTextContent("secret internals");
  });

  it("shows the snapshot first, then the claims for that same snapshot", async () => {
    const user = userEvent.setup();
    const claims = deferred<ClaimsResponse>();
    const loadClaims = vi.fn(() => claims.promise);
    render(
      <App
        loadProviders={providers}
        loadSnapshot={() => Promise.resolve(makeSnapshot())}
        loadClaims={loadClaims}
        loadVerdicts={neverChecked}
      />,
    );

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);
    expect(
      await screen.findByRole("heading", { name: "Add retry to the upload client" }),
    ).toBeInTheDocument();
    expect(loadClaims).toHaveBeenCalledWith(LINK, "claude_code");

    claims.resolve({ snapshot: makeSnapshot({ title: "Retry uploads" }), claims: makeClaimSet() });

    expect(await screen.findByRole("heading", { name: "Claims (2)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Retry uploads" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Found 2 claims.");
    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();
  });

  it("keeps the snapshot when finding claims fails", async () => {
    const user = userEvent.setup();
    const loadClaims = () =>
      Promise.reject(new ApiError("Claude Code isn't signed in.", "llm_unavailable"));
    render(
      <App
        loadProviders={providers}
        loadSnapshot={() => Promise.resolve(makeSnapshot())}
        loadClaims={loadClaims}
      />,
    );

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
    render(<App loadProviders={providers} loadSnapshot={loadSnapshot} loadClaims={loadClaims} />);

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
    expect(loadClaims).not.toHaveBeenCalled();
  });

  it("offers each model provider, defaulting to the first ready one", async () => {
    render(<App loadProviders={providers} loadSnapshot={vi.fn()} loadClaims={never} />);

    const select = await screen.findByLabelText("Model provider");
    expect(select).toHaveValue("claude_code");
    expect(select).toHaveAccessibleDescription(/Found at \/usr\/local\/bin\/claude\./);
    expect(select).toHaveAccessibleDescription(/Codex: Codex isn't installed/);
    expect(screen.getByRole("option", { name: "Codex (not ready)" })).toBeDisabled();
    expect(screen.getByRole("option", { name: "Gemini CLI" })).toBeEnabled();
  });

  it("finds claims with the chosen provider and names it with the claims", async () => {
    const user = userEvent.setup();
    const loadClaims = vi.fn(() =>
      Promise.resolve({
        snapshot: makeSnapshot(),
        claims: makeClaimSet({ provider: "gemini", model: "gemini-2.5-pro" }),
      }),
    );
    render(
      <App
        loadProviders={providers}
        loadSnapshot={() => Promise.resolve(makeSnapshot())}
        loadClaims={loadClaims}
        loadVerdicts={neverChecked}
      />,
    );

    const select = await screen.findByLabelText("Model provider");
    await user.selectOptions(select, "Gemini CLI");
    expect(select).toHaveAccessibleDescription(/free Google account/);
    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByText("Found by Gemini CLI, using gemini-2.5-pro.")).toBeVisible();
    expect(loadClaims).toHaveBeenCalledWith(LINK, "gemini");
  });

  it("can't change the provider while a review is running", async () => {
    const user = userEvent.setup();
    render(
      <App
        loadProviders={providers}
        loadSnapshot={() => Promise.resolve(makeSnapshot())}
        loadClaims={never}
      />,
    );

    await screen.findByLabelText("Model provider");
    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByLabelText("Model provider")).toBeDisabled();
  });

  it("lets the runner choose when the provider list can't be loaded", async () => {
    const user = userEvent.setup();
    const loadClaims = vi.fn(never);
    render(
      <App
        loadProviders={() => Promise.reject(new Error("offline"))}
        loadSnapshot={() => Promise.resolve(makeSnapshot())}
        loadClaims={loadClaims}
      />,
    );

    expect(await screen.findByText(/first ready one will be used/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    await screen.findByRole("heading", { name: "Add retry to the upload client" });
    expect(loadClaims).toHaveBeenCalledWith(LINK, null);
  });

  it("checks the claims after they load and announces the verdicts", async () => {
    const user = userEvent.setup();
    const checked = deferred<VerdictsResponse>();
    const loadVerdicts = vi.fn(() => checked.promise);
    const found = { snapshot: makeSnapshot(), claims: makeClaimSet() };
    render(
      <App
        loadProviders={providers}
        loadSnapshot={() => Promise.resolve(makeSnapshot())}
        loadClaims={() => Promise.resolve(found)}
        loadVerdicts={loadVerdicts}
      />,
    );

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Found 2 claims. Checking them against the diff…",
    );
    expect(loadVerdicts).toHaveBeenCalledWith(LINK, found.claims.claims);
    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();

    checked.resolve({ snapshot: makeSnapshot(), verdicts: makeVerdictSet() });

    expect(await screen.findByText("Supported")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Checked: 1 supported, 1 insufficient evidence.",
    );
    expect(screen.getByRole("button", { name: "Load pull request" })).toBeEnabled();
  });

  it("keeps the claims when checking them fails", async () => {
    const user = userEvent.setup();
    const loadVerdicts = () =>
      Promise.reject(new ApiError("Claude Code isn't signed in.", "llm_unavailable"));
    render(
      <App
        loadProviders={providers}
        loadSnapshot={() => Promise.resolve(makeSnapshot())}
        loadClaims={() => Promise.resolve({ snapshot: makeSnapshot(), claims: makeClaimSet() })}
        loadVerdicts={loadVerdicts}
      />,
    );

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't check the claims. Claude Code isn't signed in.",
    );
    expect(screen.getAllByText("Analysis failed")).toHaveLength(2);
    expect(screen.getByRole("status")).toHaveTextContent("They couldn't be checked.");
  });

  it("doesn't check anything when there are no claims", async () => {
    const user = userEvent.setup();
    const loadVerdicts = vi.fn(neverChecked);
    const empty = { snapshot: makeSnapshot(), claims: makeClaimSet({ claims: [] }) };
    render(
      <App
        loadProviders={providers}
        loadSnapshot={() => Promise.resolve(makeSnapshot())}
        loadClaims={() => Promise.resolve(empty)}
        loadVerdicts={loadVerdicts}
      />,
    );

    await user.type(screen.getByLabelText("Pull request link"), `${LINK}{Enter}`);

    expect(await screen.findByRole("heading", { name: "Claims (0)" })).toBeInTheDocument();
    expect(loadVerdicts).not.toHaveBeenCalled();
  });
});
