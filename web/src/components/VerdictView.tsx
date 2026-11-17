import type {
  CheckCitation,
  ClaimStatus,
  ClaimVerdict,
  CodeCitation,
  DiffCitation,
  PullRequestRef,
  VerdictSet,
} from "../types";
import { checkOutcome } from "./SnapshotView";

export type VerdictsState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; verdicts: VerdictSet; repo: PullRequestRef };

/** Text and an icon for every status, so the verdict never depends on colour alone. */
const STATUS_DISPLAY: Record<ClaimStatus, { label: string; icon: string; tone: string }> = {
  supported: { label: "Supported", icon: "✓", tone: "pass" },
  contradicted: { label: "Contradicted", icon: "✗", tone: "fail" },
  insufficient_evidence: { label: "Insufficient evidence", icon: "?", tone: "pending" },
  analysis_failed: { label: "Analysis failed", icon: "!", tone: "neutral" },
};

export const STATUS_ORDER: ClaimStatus[] = [
  "supported",
  "contradicted",
  "insufficient_evidence",
  "analysis_failed",
];

/** The runner may be newer than this page; a status it doesn't know shows as analysis failed. */
function statusDisplay(status: ClaimStatus) {
  return Object.hasOwn(STATUS_DISPLAY, status)
    ? STATUS_DISPLAY[status]
    : STATUS_DISPLAY.analysis_failed;
}

export const statusLabel = (status: ClaimStatus) => statusDisplay(status).label;

const COMMIT_SHA = /^[0-9a-f]{40}$/;
// GitHub renders these as documents, where line anchors only work in the plain view.
const RENDERED_DOCS = /\.(md|markdown|mdx|rst|adoc|org)$/i;

type LineCitation = DiffCitation | CodeCitation;

/** A link to the cited lines at the reviewed commit, or null if the commit id isn't one. */
export function citationUrl(
  repo: PullRequestRef,
  headSha: string,
  citation: LineCitation,
): string | null {
  if (!COMMIT_SHA.test(headSha)) return null;
  const path = citation.path.split("/").map(encodeURIComponent).join("/");
  const plain = RENDERED_DOCS.test(citation.path) ? "?plain=1" : "";
  const lines = `L${String(citation.start_line)}-L${String(citation.end_line)}`;
  const owner = encodeURIComponent(repo.owner);
  const name = encodeURIComponent(repo.repo);
  return `https://github.com/${owner}/${name}/blob/${headSha}/${path}${plain}#${lines}`;
}

function lineRange({ start_line: start, end_line: end }: LineCitation) {
  return start === end ? `line ${String(start)}` : `lines ${String(start)}–${String(end)}`;
}

export function StatusBadge({ status }: { status: ClaimStatus }) {
  const { label, icon, tone } = statusDisplay(status);
  return (
    <span className={`badge verdict-badge ${tone}`}>
      <span aria-hidden="true">{icon}</span> {label}
    </span>
  );
}

function DiffCitationItem({
  citation,
  repo,
  headSha,
}: {
  citation: LineCitation;
  repo: PullRequestRef;
  headSha: string;
}) {
  const url = citationUrl(repo, headSha, citation);
  const label = (
    <>
      <code>{citation.path}</code>, {lineRange(citation)}
      {citation.kind === "code" && " (repository code)"}
    </>
  );
  if (!url) return <li>{label}</li>;
  return (
    <li>
      <a href={url} target="_blank" rel="noreferrer">
        {label}
        <span className="visually-hidden"> (opens GitHub in a new tab)</span>
      </a>
    </li>
  );
}

function CheckCitationItem({ citation }: { citation: CheckCitation }) {
  return (
    <li>
      CI check <code>{citation.name}</code>: {checkOutcome(citation).label}
    </li>
  );
}

function TextList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <>
      <h4>{title}</h4>
      <ul>
        {items.map((item, index) => (
          <li key={String(index)}>{item}</li>
        ))}
      </ul>
    </>
  );
}

export function ClaimVerdictView({
  verdict,
  repo,
  headSha,
}: {
  verdict: ClaimVerdict;
  repo: PullRequestRef;
  headSha: string;
}) {
  const { citations } = verdict;
  return (
    <div className="verdict">
      <p className="verdict-status">
        <StatusBadge status={verdict.status} />
      </p>
      {verdict.explanation && <p>{verdict.explanation}</p>}
      {verdict.notes.map((note) => (
        <p key={note} className="note">
          {note}
        </p>
      ))}
      {citations.length > 0 && (
        <>
          <h4>Evidence</h4>
          <ul className="citations">
            {citations.map((citation, index) =>
              citation.kind === "diff" || citation.kind === "code" ? (
                <DiffCitationItem
                  key={String(index)}
                  citation={citation}
                  repo={repo}
                  headSha={headSha}
                />
              ) : (
                <CheckCitationItem key={String(index)} citation={citation} />
              ),
            )}
          </ul>
        </>
      )}
      <TextList title="Missing tests" items={verdict.missing_tests} />
      <TextList title="Edge cases to check" items={verdict.edge_cases} />
    </div>
  );
}

/** Shown for a claim when no verdict exists: the model skipped it or the check failed. */
export function AnalysisFailed({ reason }: { reason: string }) {
  return (
    <div className="verdict">
      <p className="verdict-status">
        <StatusBadge status="analysis_failed" />
      </p>
      <p className="note">{reason}</p>
    </div>
  );
}
