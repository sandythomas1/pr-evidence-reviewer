import type { Claim, ClaimKind, ClaimSet } from "../types";
import { AnalysisFailed, ClaimVerdictView, type VerdictsState } from "./VerdictView";

const KIND_LABELS: Record<ClaimKind, string> = {
  behavior: "Behavior",
  test: "Tests",
  performance: "Performance",
  security: "Security",
  refactor: "Refactor",
  docs: "Docs",
  other: "Other",
};

const NOT_ASSESSED = "The model didn't return an assessment for this claim.";
const NOT_CHECKED = "This claim couldn't be checked, so it has no verdict.";

export type ClaimsState =
  { kind: "loading" } | { kind: "error"; message: string } | { kind: "loaded"; claims: ClaimSet };

interface Props {
  state: ClaimsState;
  verdicts?: VerdictsState;
  /** The display name for the provider id in the claim set, when the runner listed it. */
  providerName?: string | null;
  /** The same, for the provider id in the verdict set. */
  verdictsProviderName?: string | null;
}

/** For example "Found by Claude Code, using claude-opus-5-5." */
function attribution(
  verb: "Found" | "Checked",
  providerName: string | null,
  provider: string | null,
  model: string | null,
) {
  if (providerName)
    return model ? `${verb} by ${providerName}, using ${model}.` : `${verb} by ${providerName}.`;
  const fallback = model ?? provider;
  return fallback ? `${verb} by ${fallback}.` : null;
}

function ClaimAssessment({ claim, verdicts }: { claim: Claim; verdicts: VerdictsState }) {
  if (verdicts.kind === "error") return <AnalysisFailed reason={NOT_CHECKED} />;
  if (verdicts.kind !== "loaded") return null;
  const { verdicts: set, repo } = verdicts;
  const verdict = set.verdicts.find((item) => item.claim_id === claim.id);
  if (!verdict) return <AnalysisFailed reason={NOT_ASSESSED} />;
  return <ClaimVerdictView verdict={verdict} repo={repo} headSha={set.head_sha} />;
}

function VerdictsSummary({ verdicts }: { verdicts: VerdictsState }) {
  if (verdicts.kind === "idle") {
    return (
      <p className="hint">
        What the pull request says it does, in the author&apos;s words. None of these has been
        checked against the code yet.
      </p>
    );
  }
  if (verdicts.kind === "loading") {
    return (
      <p className="hint">
        Checking each claim against the diff, the repository&apos;s code and tests, and the CI
        checks. The first review of a repository can take a few minutes…
      </p>
    );
  }
  if (verdicts.kind === "error") {
    return (
      <div className="error" role="alert">
        Couldn&apos;t check the claims. {verdicts.message}
      </div>
    );
  }

  const { notes, scope_limitations: notChecked } = verdicts.verdicts;
  return (
    <>
      <p className="hint">
        Each verdict covers only what was checked at this commit: the diff, a search of the
        repository&apos;s code, its tests, the functions and classes around each change, and the CI
        checks. Reading code can&apos;t prove how it runs, and a test isn&apos;t proof that it ran.
        You make the approval decision.
      </p>
      {notes.map((note) => (
        <p key={note} className="note">
          {note}
        </p>
      ))}
      {notChecked.length > 0 && (
        <section aria-labelledby="verdicts-gaps-title" className="gaps">
          <h4 id="verdicts-gaps-title">Not checked</h4>
          <ul>
            {notChecked.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function Body({
  state,
  verdicts = { kind: "idle" },
  providerName = null,
  verdictsProviderName = null,
}: Props) {
  if (state.kind === "loading") {
    return <p className="hint">Reading the description and pulling out its claims…</p>;
  }
  if (state.kind === "error") {
    return (
      <div className="error" role="alert">
        Couldn&apos;t pull out the claims. {state.message}
      </div>
    );
  }

  const { claims, notes, scope_limitations: notRead, model, provider } = state.claims;
  const found = attribution("Found", providerName, provider, model);
  const checked =
    verdicts.kind === "loaded"
      ? attribution(
          "Checked",
          verdictsProviderName,
          verdicts.verdicts.provider,
          verdicts.verdicts.model,
        )
      : null;
  return (
    <>
      {claims.length > 0 && <VerdictsSummary verdicts={verdicts} />}
      {notes.map((note) => (
        <p key={note} className="note">
          {note}
        </p>
      ))}
      {notRead.length > 0 && (
        <section aria-labelledby="claims-gaps-title" className="gaps">
          <h4 id="claims-gaps-title">Not read</h4>
          <ul>
            {notRead.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </section>
      )}
      {claims.length > 0 && (
        <ol className="claims">
          {claims.map((claim) => (
            <li key={claim.id} className="claim">
              <span className="badge neutral">{KIND_LABELS[claim.kind]}</span>
              <p className="statement">{claim.statement}</p>
              <blockquote>{claim.quote}</blockquote>
              <ClaimAssessment claim={claim} verdicts={verdicts} />
            </li>
          ))}
        </ol>
      )}
      {found && <p className="hint">{found}</p>}
      {checked && <p className="hint">{checked}</p>}
    </>
  );
}

export function ClaimsView({
  state,
  verdicts = { kind: "idle" },
  providerName = null,
  verdictsProviderName = null,
}: Props) {
  const count = state.kind === "loaded" ? ` (${String(state.claims.claims.length)})` : "";
  return (
    <section
      className="claims-section"
      aria-labelledby="claims-title"
      aria-busy={state.kind === "loading" || verdicts.kind === "loading"}
    >
      <h3 id="claims-title">Claims{count}</h3>
      <Body
        state={state}
        verdicts={verdicts}
        providerName={providerName}
        verdictsProviderName={verdictsProviderName}
      />
    </section>
  );
}
