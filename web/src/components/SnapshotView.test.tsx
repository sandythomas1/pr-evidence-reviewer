import { render, screen, within } from "@testing-library/react";
import { makeSnapshot } from "../test/fixtures";
import { SnapshotView } from "./SnapshotView";

describe("SnapshotView", () => {
  it("shows the title, repository, and the commits the review is pinned to", () => {
    render(<SnapshotView snapshot={makeSnapshot()} />);

    expect(
      screen.getByRole("heading", { level: 2, name: "Add retry to the upload client" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/octo-org\/widgets · #42/)).toBeInTheDocument();
    expect(screen.getByText("bbbbbbb")).toHaveAttribute("title", "b".repeat(40));
    expect(screen.getAllByText("aaaaaaa")[0]).toHaveAttribute("title", "a".repeat(40));
  });

  it("lists changed files with line counts and renames", () => {
    render(<SnapshotView snapshot={makeSnapshot()} />);

    const [, upload, renamed, ...extra] = within(screen.getByRole("table")).getAllByRole("row");
    if (!upload || !renamed) throw new Error("expected a header row and two file rows");
    expect(extra).toHaveLength(0);
    expect(within(upload).getByText("src/upload.py")).toBeInTheDocument();
    expect(within(upload).getByText("+12")).toBeInTheDocument();
    expect(within(upload).getByText("−3")).toBeInTheDocument();
    expect(within(renamed).getByText(/was tests\/test_client.py/)).toBeInTheDocument();
  });

  it("labels CI checks in plain language", () => {
    render(<SnapshotView snapshot={makeSnapshot()} />);

    const checks = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(checks.map((item) => item.textContent)).toEqual([
      "passed lint",
      "failure tests",
      "pending deploy-preview",
    ]);
  });

  it("says so when there are no CI results instead of implying they passed", () => {
    render(<SnapshotView snapshot={makeSnapshot({ checks: [] })} />);

    expect(screen.getByText(/No CI results were reported/)).toBeInTheDocument();
  });

  it("shows what wasn't inspected", () => {
    const gap = "GitHub listed 3000 of 3500 changed files; the rest weren't inspected.";
    render(<SnapshotView snapshot={makeSnapshot({ scope_limitations: [gap] })} />);

    const section = screen.getByRole("region", { name: "Not inspected" });
    expect(within(section).getByText(gap)).toBeInTheDocument();
  });

  it("hides the gaps section when everything was inspected", () => {
    render(<SnapshotView snapshot={makeSnapshot()} />);

    expect(screen.queryByRole("region", { name: "Not inspected" })).not.toBeInTheDocument();
  });
});
