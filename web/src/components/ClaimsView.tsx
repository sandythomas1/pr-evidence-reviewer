import type { ClaimKind, ClaimSet } from "../types";

const KIND_LABELS: Record<ClaimKind, string> = {
  behavior: "Behavior",
  test: "Tests",
  performance: "Performance",
  security: "Security",
  refactor: "Refactor",
  docs: "Docs",
  other: "Other",
};

export type ClaimsState =
  { kind: "loading" } | { kind: "error"; message: string } | { kind: "loaded"; claims: ClaimSet };

function Body({ state }: { state: ClaimsState }) {
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
  return (
    <>
      {claims.length > 0 && (
        <p className="hint">
          What the pull request says it does, in the author&apos;s words. None of these has been
          checked against the code yet.
        </p>
      )}
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
            </li>
          ))}
        </ol>
      )}
      {(model ?? provider) && <p className="hint">Found by {model ?? provider}.</p>}
    </>
  );
}

export function ClaimsView({ state }: { state: ClaimsState }) {
  const count = state.kind === "loaded" ? ` (${String(state.claims.claims.length)})` : "";
  return (
    <section
      className="claims-section"
      aria-labelledby="claims-title"
      aria-busy={state.kind === "loading"}
    >
      <h3 id="claims-title">Claims{count}</h3>
      <Body state={state} />
    </section>
  );
}
