import { render, screen, within } from "@testing-library/react";
import { makeClaimSet, makeSnapshot, makeVerdictSet } from "../test/fixtures";
import { ClaimsView } from "./ClaimsView";

describe("ClaimsView", () => {
  it("lists each claim with its kind, statement, and the author's exact words", () => {
    render(<ClaimsView state={{ kind: "loaded", claims: makeClaimSet() }} />);

    expect(screen.getByRole("heading", { name: "Claims (2)" })).toBeInTheDocument();
    const items = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    const [first] = items;
    if (!first) throw new Error("expected a claim");
    expect(first).toHaveTextContent("Behavior");
    expect(first).toHaveTextContent("A failed upload is retried up to three times.");
    expect(within(first).getByText("Retries failed uploads three times.").tagName).toBe(
      "BLOCKQUOTE",
    );
    expect(screen.getByText(/None of these has been checked against the code yet/)).toBeVisible();
    expect(screen.getByText("Found by claude-opus-5-5.")).toBeInTheDocument();
  });

  it("explains an empty description instead of showing an empty list", () => {
    const note = "This pull request has no description, so there are no claims to check.";
    render(
      <ClaimsView
        state={{
          kind: "loaded",
          claims: makeClaimSet({ claims: [], notes: [note], model: null, provider: null }),
        }}
      />,
    );

    expect(screen.getByRole("heading", { name: "Claims (0)" })).toBeInTheDocument();
    expect(screen.getByText(note)).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.queryByText(/Found by/)).not.toBeInTheDocument();
  });

  it("shows dropped claims and unread text as gaps", () => {
    const claims = makeClaimSet({
      dropped_count: 1,
      notes: ["1 claim was left out because the quoted text doesn't appear in the pull request."],
      scope_limitations: ["The description is 25,000 characters long."],
    });
    render(<ClaimsView state={{ kind: "loaded", claims }} />);

    expect(screen.getByText(/1 claim was left out/)).toBeInTheDocument();
    const gaps = screen.getByRole("region", { name: "Not read" });
    expect(gaps).toHaveTextContent("The description is 25,000 characters long.");
  });

  it("marks the section busy while the model works", () => {
    render(<ClaimsView state={{ kind: "loading" }} />);

    const section = screen.getByRole("region", { name: "Claims" });
    expect(section).toHaveAttribute("aria-busy", "true");
    expect(section).toHaveTextContent("pulling out its claims");
  });

  it("shows a claims error as an alert inside the section", () => {
    render(<ClaimsView state={{ kind: "error", message: "Claude Code isn't signed in." }} />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't pull out the claims. Claude Code isn't signed in.",
    );
  });

  it("names the provider and model that found the claims", () => {
    render(
      <ClaimsView state={{ kind: "loaded", claims: makeClaimSet() }} providerName="Claude Code" />,
    );

    expect(screen.getByText("Found by Claude Code, using claude-opus-5-5.")).toBeInTheDocument();
  });

  it("names just the provider when the model isn't known", () => {
    render(
      <ClaimsView
        state={{ kind: "loaded", claims: makeClaimSet({ provider: "codex", model: null }) }}
        providerName="Codex"
      />,
    );

    expect(screen.getByText("Found by Codex.")).toBeInTheDocument();
  });

  describe("with verdicts", () => {
    const claims = { kind: "loaded", claims: makeClaimSet() } as const;
    const repo = makeSnapshot().ref;

    function claimItem(index: number) {
      const item = document.querySelectorAll<HTMLElement>("li.claim")[index];
      if (!item) throw new Error("expected a claim");
      return item;
    }

    it("shows each claim's verdict under it, with the set's notes and limits", () => {
      const verdicts = makeVerdictSet({ scope_limitations: ["No diff is available for a.png."] });
      render(<ClaimsView state={claims} verdicts={{ kind: "loaded", verdicts, repo }} />);

      expect(claimItem(0)).toHaveTextContent("Supported");
      expect(within(claimItem(0)).getByRole("link")).toHaveAttribute(
        "href",
        expect.stringContaining("/octo-org/widgets/blob/") as string,
      );
      expect(claimItem(1)).toHaveTextContent("Insufficient evidence");
      expect(screen.getByText(/1 citation in all was left out/)).toBeInTheDocument();
      expect(screen.getByText(/You make the approval decision/)).toHaveTextContent(
        "Each verdict covers only the evidence the reviewer could read at this commit: the diff " +
          "and the CI checks, plus, when the repository could be read, a search of its code, its " +
          "tests, and the functions and classes around each change. Anything under “Not checked” " +
          "wasn't used.",
      );
      expect(screen.getByRole("region", { name: "Not checked" })).toHaveTextContent(
        "No diff is available for a.png.",
      );
      expect(screen.getByText("Checked by claude-opus-5-5.")).toBeInTheDocument();
      expect(screen.queryByText(/None of these has been checked/)).not.toBeInTheDocument();
    });

    it("names the provider and model that checked the claims", () => {
      const verdicts = makeVerdictSet({ provider: "codex", model: "gpt-5-codex" });
      render(
        <ClaimsView
          state={claims}
          verdicts={{ kind: "loaded", verdicts, repo }}
          verdictsProviderName="Codex"
        />,
      );

      expect(screen.getByText("Checked by Codex, using gpt-5-codex.")).toBeInTheDocument();
    });

    it("marks a claim the model skipped as analysis failed", () => {
      const verdicts = makeVerdictSet();
      verdicts.verdicts = verdicts.verdicts.slice(0, 1);
      render(<ClaimsView state={claims} verdicts={{ kind: "loaded", verdicts, repo }} />);

      expect(claimItem(1)).toHaveTextContent("Analysis failed");
      expect(claimItem(1)).toHaveTextContent(
        "The model didn't return an assessment for this claim.",
      );
    });

    it("keeps the claims and marks each one when checking fails", () => {
      render(
        <ClaimsView
          state={claims}
          verdicts={{ kind: "error", message: "Claude Code isn't signed in." }}
        />,
      );

      expect(screen.getByRole("alert")).toHaveTextContent(
        "Couldn't check the claims. Claude Code isn't signed in.",
      );
      expect(screen.getAllByText("Analysis failed")).toHaveLength(2);
      expect(screen.getByText("A failed upload is retried up to three times.")).toBeVisible();
    });

    it("marks the section busy while the claims are checked", () => {
      render(<ClaimsView state={claims} verdicts={{ kind: "loading" }} />);

      const section = screen.getByRole("region", { name: "Claims (2)" });
      expect(section).toHaveAttribute("aria-busy", "true");
      expect(section).toHaveTextContent(
        "Checking each claim against the diff, the repository's code and tests, and the CI " +
          "checks. The first review of a commit can take a few minutes…",
      );
    });
  });
});
