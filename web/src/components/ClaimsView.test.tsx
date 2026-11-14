import { render, screen, within } from "@testing-library/react";
import { makeClaimSet } from "../test/fixtures";
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
});
