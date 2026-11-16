import { prLinkProblem } from "./prUrl";

describe("prLinkProblem", () => {
  it.each([
    "https://github.com/octo-org/widgets/pull/42",
    "  https://www.github.com/octo-org/widgets.js/pull/7/files  ",
    "https://github.com/octo-org/widgets/pull/42#discussion_r1",
  ])("accepts %s", (link) => {
    expect(prLinkProblem(link)).toBeNull();
  });

  it("asks for a link when the field is empty", () => {
    expect(prLinkProblem("   ")).toMatch(/Enter a pull request link/);
  });

  it.each([
    "http://github.com/octo-org/widgets/pull/42",
    "https://gitlab.com/octo-org/widgets/pull/42",
    "https://github.com/octo-org/widgets/issues/42",
    "https://github.com/octo-org/widgets/pull/abc",
    "github.com/octo-org/widgets/pull/42",
  ])("rejects %s", (link) => {
    expect(prLinkProblem(link)).toMatch(/doesn't look like a GitHub pull request link/);
  });
});
