/** Mirrors the runner's pydantic models in pr_evidence_reviewer.domain.pull_request. */

export interface PullRequestRef {
  owner: string;
  repo: string;
  number: number;
}

export interface CommitRef {
  ref: string;
  sha: string;
}

export interface ChangedFile {
  path: string;
  status: string;
  additions: number;
  deletions: number;
  previous_path: string | null;
  patch: string | null;
}

export interface CheckResult {
  name: string;
  source: "check_run" | "status";
  status: string;
  conclusion: string | null;
  url: string | null;
}

export interface PullRequestSnapshot {
  ref: PullRequestRef;
  title: string;
  body: string;
  author: string | null;
  state: string;
  draft: boolean;
  html_url: string;
  base: CommitRef;
  head: CommitRef;
  changed_files_count: number;
  files: ChangedFile[];
  checks: CheckResult[];
  scope_limitations: string[];
}

/** Mirrors pr_evidence_reviewer.domain.claims. */
export type ClaimKind =
  "behavior" | "test" | "performance" | "security" | "refactor" | "docs" | "other";

export interface Claim {
  id: string;
  quote: string;
  statement: string;
  kind: ClaimKind;
}

export interface ClaimSet {
  claims: Claim[];
  dropped_count: number;
  notes: string[];
  scope_limitations: string[];
  provider: string | null;
  model: string | null;
}

export interface ClaimsResponse {
  snapshot: PullRequestSnapshot;
  claims: ClaimSet;
}
