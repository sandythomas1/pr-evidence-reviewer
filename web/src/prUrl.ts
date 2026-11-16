/**
 * A quick shape check so obvious typos get instant feedback.
 * The runner re-validates every link; this is a convenience, not a security boundary.
 */
const PR_LINK =
  /^https:\/\/(www\.)?github\.com\/[A-Za-z0-9-]+\/[A-Za-z0-9._-]+\/pull\/\d+\/?(?:[/?#].*)?$/;

export const EXPECTED_SHAPE = "Paste a link like https://github.com/owner/repo/pull/123.";

export function prLinkProblem(value: string): string | null {
  const text = value.trim();
  if (text === "") return `Enter a pull request link. ${EXPECTED_SHAPE}`;
  if (!PR_LINK.test(text)) {
    return `That doesn't look like a GitHub pull request link. ${EXPECTED_SHAPE}`;
  }
  return null;
}
