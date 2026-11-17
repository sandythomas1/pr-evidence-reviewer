import type {
  ClaimSet,
  CodeCitation,
  ProviderStatus,
  PullRequestSnapshot,
  VerdictSet,
} from "../types";

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

export function makeClaimSet(overrides: Partial<ClaimSet> = {}): ClaimSet {
  return {
    claims: [
      {
        id: "c1",
        quote: "Retries failed uploads three times.",
        statement: "A failed upload is retried up to three times.",
        kind: "behavior",
      },
      {
        id: "c2",
        quote: "Adds a unit test for the retry loop.",
        statement: "A unit test covers the retry loop.",
        kind: "test",
      },
    ],
    dropped_count: 0,
    notes: [],
    scope_limitations: [],
    provider: "claude_code",
    model: "claude-opus-5-5",
    ...overrides,
  };
}

export function makeProviders(): ProviderStatus[] {
  return [
    {
      id: "claude_code",
      name: "Claude Code",
      available: true,
      detail: "Found at /usr/local/bin/claude.",
    },
    {
      id: "codex",
      name: "Codex",
      available: false,
      detail: "Codex isn't installed. Install the Codex CLI, run `codex login`, then try again.",
    },
    {
      id: "gemini",
      name: "Gemini CLI",
      available: true,
      detail:
        "Found at /usr/local/bin/gemini. With a free Google account, Google may use what you " +
        "send to improve its products, so avoid it for private repositories.",
    },
  ];
}

export function makeVerdictSet(overrides: Partial<VerdictSet> = {}): VerdictSet {
  return {
    head_sha: HEAD_SHA,
    verdicts: [
      {
        claim_id: "c1",
        status: "supported",
        explanation: "The upload loop now tries three times before giving up.",
        citations: [
          { kind: "diff", path: "src/upload.py", start_line: 10, end_line: 14 },
          {
            kind: "check",
            name: "lint",
            source: "check_run",
            status: "completed",
            conclusion: "success",
          },
        ],
        missing_tests: ["A test where every attempt fails."],
        edge_cases: ["A timeout on the last attempt."],
        dropped_citations: 0,
        notes: [],
      },
      {
        claim_id: "c2",
        status: "insufficient_evidence",
        explanation: "The test file changed, but the tests check failed.",
        citations: [],
        missing_tests: [],
        edge_cases: [],
        dropped_citations: 1,
        notes: [
          "1 citation was left out because it doesn't point to lines shown in the evidence or to a CI check.",
        ],
      },
    ],
    dropped_citations: 1,
    notes: ["1 citation in all was left out because it didn't match the evidence or a CI check."],
    scope_limitations: [],
    provider: "claude_code",
    model: "claude-opus-5-5",
    packet_hash: null,
    ...overrides,
  };
}

export function makeCodeCitation(overrides: Partial<CodeCitation> = {}): CodeCitation {
  return {
    kind: "code",
    path: "src/retry.py",
    start_line: 3,
    end_line: 8,
    commit: HEAD_SHA,
    ...overrides,
  };
}
