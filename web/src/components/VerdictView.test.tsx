import { render, screen, within } from "@testing-library/react";
import { HEAD_SHA, makeVerdictSet } from "../test/fixtures";
import type { ClaimVerdict, DiffCitation } from "../types";
import { ClaimVerdictView, StatusBadge, citationUrl } from "./VerdictView";

const REPO = { owner: "octo-org", repo: "widgets", number: 42 };
const cite = (path: string, start: number, end = start): DiffCitation => ({
  kind: "diff",
  path,
  start_line: start,
  end_line: end,
});

function firstVerdict(overrides: Partial<ClaimVerdict> = {}): ClaimVerdict {
  const [first] = makeVerdictSet().verdicts;
  if (!first) throw new Error("expected a verdict");
  return { ...first, ...overrides };
}

describe("citationUrl", () => {
  it("links to the cited lines at the reviewed commit", () => {
    expect(citationUrl(REPO, HEAD_SHA, cite("src/upload.py", 10, 14))).toBe(
      `https://github.com/octo-org/widgets/blob/${HEAD_SHA}/src/upload.py#L10-L14`,
    );
  });

  it("encodes each path segment so a file name can't change the link", () => {
    expect(citationUrl(REPO, HEAD_SHA, cite("docs/a b/#x?.py", 1))).toBe(
      `https://github.com/octo-org/widgets/blob/${HEAD_SHA}/docs/a%20b/%23x%3F.py#L1-L1`,
    );
  });

  it("uses the plain view for files GitHub renders as documents", () => {
    expect(citationUrl(REPO, HEAD_SHA, cite("README.md", 3))).toMatch(
      /\/README\.md\?plain=1#L3-L3$/,
    );
  });

  it("refuses a commit id that isn't one", () => {
    expect(citationUrl(REPO, "main", cite("a.py", 1))).toBeNull();
    expect(citationUrl(REPO, `${HEAD_SHA}/../x`, cite("a.py", 1))).toBeNull();
  });
});

describe("StatusBadge", () => {
  it.each([
    ["supported", "✓", "Supported"],
    ["contradicted", "✗", "Contradicted"],
    ["insufficient_evidence", "?", "Insufficient evidence"],
    ["analysis_failed", "!", "Analysis failed"],
  ] as const)("shows %s as text with a decorative icon", (status, icon, label) => {
    render(<StatusBadge status={status} />);

    const badge = screen.getByText(label, { exact: false });
    expect(badge).toHaveTextContent(`${icon} ${label}`);
    expect(within(badge).getByText(icon)).toHaveAttribute("aria-hidden", "true");
  });
});

describe("ClaimVerdictView", () => {
  it("shows the verdict, explanation, linked evidence, missing tests, and edge cases", () => {
    render(<ClaimVerdictView verdict={firstVerdict()} repo={REPO} headSha={HEAD_SHA} />);

    expect(screen.getByText("Supported")).toBeInTheDocument();
    expect(
      screen.getByText("The upload loop now tries three times before giving up."),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /src\/upload\.py, lines 10–14/ });
    expect(link).toHaveAttribute(
      "href",
      `https://github.com/octo-org/widgets/blob/${HEAD_SHA}/src/upload.py#L10-L14`,
    );
    expect(link).toHaveAttribute("rel", "noreferrer");
    expect(link).toHaveAccessibleName(/opens GitHub in a new tab/);
    expect(screen.getByText(/CI check/)).toHaveTextContent("CI check lint: passed");
    expect(screen.getByRole("heading", { name: "Missing tests" })).toBeInTheDocument();
    expect(screen.getByText("A test where every attempt fails.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Edge cases to check" })).toBeInTheDocument();
  });

  it("names a single cited line", () => {
    const verdict = firstVerdict({ citations: [cite("src/upload.py", 7)] });
    render(<ClaimVerdictView verdict={verdict} repo={REPO} headSha={HEAD_SHA} />);

    expect(screen.getByRole("link", { name: /src\/upload\.py, line 7/ })).toBeInTheDocument();
  });

  it("shows a citation without a link when the commit id isn't valid", () => {
    render(<ClaimVerdictView verdict={firstVerdict()} repo={REPO} headSha="not-a-sha" />);

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("src/upload.py")).toBeInTheDocument();
  });

  it("leaves out empty sections and shows the notes", () => {
    const [, second] = makeVerdictSet().verdicts;
    if (!second) throw new Error("expected a verdict");
    render(<ClaimVerdictView verdict={second} repo={REPO} headSha={HEAD_SHA} />);

    expect(screen.getByText("Insufficient evidence")).toBeInTheDocument();
    expect(screen.getByText(/1 citation was left out/)).toBeInTheDocument();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("shows a status it doesn't know as analysis failed instead of crashing", () => {
    const verdict = firstVerdict({ status: "approved" as ClaimVerdict["status"] });
    render(<ClaimVerdictView verdict={verdict} repo={REPO} headSha={HEAD_SHA} />);

    expect(screen.getByText("Analysis failed")).toBeInTheDocument();
  });

  it("shows model text as text, never as markup", () => {
    const verdict = firstVerdict({ explanation: "<img src=x onerror=alert(1)>" });
    const { container } = render(
      <ClaimVerdictView verdict={verdict} repo={REPO} headSha={HEAD_SHA} />,
    );

    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});
