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

/** Mirrors pr_evidence_reviewer.llm.base.ProviderStatus. */
export interface ProviderStatus {
  id: string;
  name: string;
  available: boolean;
  detail: string;
}

/** Mirrors pr_evidence_reviewer.domain.verdicts. */
export type ClaimStatus =
  "supported" | "contradicted" | "insufficient_evidence" | "analysis_failed";

export interface DiffCitation {
  kind: "diff";
  path: string;
  start_line: number;
  end_line: number;
}

/** Lines of a file at the head commit, which the pull request may not have changed. */
export interface CodeCitation {
  kind: "code";
  path: string;
  start_line: number;
  end_line: number;
  commit: string;
}

export interface CheckCitation {
  kind: "check";
  name: string;
  source: "check_run" | "status";
  status: string;
  conclusion: string | null;
}

export type Citation = DiffCitation | CodeCitation | CheckCitation;

export interface ClaimVerdict {
  claim_id: string;
  status: ClaimStatus;
  explanation: string;
  citations: Citation[];
  missing_tests: string[];
  edge_cases: string[];
  dropped_citations: number;
  notes: string[];
}

export interface VerdictSet {
  head_sha: string;
  verdicts: ClaimVerdict[];
  dropped_citations: number;
  notes: string[];
  scope_limitations: string[];
  provider: string | null;
  model: string | null;
  packet_hash: string | null;
}

export interface VerdictsResponse {
  snapshot: PullRequestSnapshot;
  verdicts: VerdictSet;
}
