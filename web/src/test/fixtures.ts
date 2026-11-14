import type { PullRequestSnapshot } from "../types";

export const HEAD_SHA = "a".repeat(40);
export const BASE_SHA = "b".repeat(40);

export function makeSnapshot(overrides: Partial<PullRequestSnapshot> = {}): PullRequestSnapshot {
  return {
    ref: { owner: "octo-org", repo: "widgets", number: 42 },
    title: "Add retry to the upload client",
    body: "Retries failed uploads three times.",
    author: "octocat",
    state: "open",
    draft: false,
    html_url: "https://github.com/octo-org/widgets/pull/42",
    base: { ref: "main", sha: BASE_SHA },
    head: { ref: "feature/retry", sha: HEAD_SHA },
    changed_files_count: 2,
    files: [
      {
        path: "src/upload.py",
        status: "modified",
        additions: 12,
        deletions: 3,
        previous_path: null,
        patch: "@@",
      },
      {
        path: "tests/test_upload.py",
        status: "renamed",
        additions: 20,
        deletions: 0,
        previous_path: "tests/test_client.py",
        patch: "@@",
      },
    ],
    checks: [
      { name: "lint", source: "check_run", status: "completed", conclusion: "success", url: null },
      { name: "tests", source: "check_run", status: "completed", conclusion: "failure", url: null },
      { name: "deploy-preview", source: "status", status: "pending", conclusion: null, url: null },
    ],
    scope_limitations: [],
    ...overrides,
  };
}
