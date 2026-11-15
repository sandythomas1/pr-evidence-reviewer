import { useEffect, useState } from "react";
import { ApiError, fetchClaims, fetchProviders, fetchSnapshot, fetchVerdicts } from "./api";
import { ClaimsView, type ClaimsState } from "./components/ClaimsView";
import { PrLinkForm } from "./components/PrLinkForm";
import { defaultProvider, ProviderSelect, type ProvidersState } from "./components/ProviderSelect";
import { SnapshotView } from "./components/SnapshotView";
import { STATUS_ORDER, statusLabel, type VerdictsState } from "./components/VerdictView";
import type {
  Claim,
  ClaimsResponse,
  ProviderStatus,
  PullRequestSnapshot,
  VerdictSet,
  VerdictsResponse,
} from "./types";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "loaded";
      snapshot: PullRequestSnapshot;
      claims: ClaimsState;
      verdicts: VerdictsState;
    };

interface Props {
  loadSnapshot?: (prUrl: string) => Promise<PullRequestSnapshot>;
  loadClaims?: (prUrl: string, providerId: string | null) => Promise<ClaimsResponse>;
  loadVerdicts?: (
    prUrl: string,
    claims: Claim[],
    providerId: string | null,
  ) => Promise<VerdictsResponse>;
  loadProviders?: () => Promise<ProviderStatus[]>;
}

const errorMessage = (error: unknown) =>
  error instanceof ApiError ? error.message : "Something went wrong. Try again.";

function announcement(state: State): string {
  switch (state.kind) {
    case "loading":
      return "Loading the pull request from GitHub…";
    case "loaded": {
      const { owner, repo, number } = state.snapshot.ref;
      const name = `${owner}/${repo} #${String(number)}`;
      if (state.claims.kind === "loading") return `Loaded ${name}. Finding its claims…`;
      if (state.claims.kind === "error") return `Loaded ${name}. Its claims couldn't be found.`;
      const count = state.claims.claims.claims.length;
      const found = `Loaded ${name}. Found ${String(count)} ${count === 1 ? "claim" : "claims"}.`;
      if (state.verdicts.kind === "loading") return `${found} Checking them against the diff…`;
      if (state.verdicts.kind === "error") return `${found} They couldn't be checked.`;
      if (state.verdicts.kind === "loaded")
        return `${found} ${verdictSummary(state.verdicts.verdicts)}`;
      return found;
    }
    default:
      return "";
  }
}

function providerName(providers: ProvidersState, providerId: string | null): string | null {
  if (providers.kind !== "loaded" || providerId === null) return null;
  return providers.providers.find((provider) => provider.id === providerId)?.name ?? null;
}

/** For example "Checked: 1 supported, 1 insufficient evidence." */
function verdictSummary(set: VerdictSet): string {
  const counts = STATUS_ORDER.map((status) => {
    const count = set.verdicts.filter((verdict) => verdict.status === status).length;
    return count ? `${String(count)} ${statusLabel(status).toLowerCase()}` : null;
  }).filter((part) => part !== null);
  return counts.length ? `Checked: ${counts.join(", ")}.` : "Checked.";
}

export function App({
  loadSnapshot = fetchSnapshot,
  loadClaims = fetchClaims,
  loadVerdicts = fetchVerdicts,
  loadProviders = fetchProviders,
}: Props) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const [providers, setProviders] = useState<ProvidersState>({ kind: "loading" });
  const [providerId, setProviderId] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    loadProviders().then(
      (found) => {
        if (!current) return;
        setProviders({ kind: "loaded", providers: found });
        setProviderId(defaultProvider(found));
      },
      () => {
        if (current) setProviders({ kind: "error" });
      },
    );
    return () => {
      current = false;
    };
  }, [loadProviders]);

  async function load(prUrl: string) {
    // One review uses one provider: the choice when it started, for the claims and the verdicts.
    const chosenProvider = providerId;
    setState({ kind: "loading" });
    let snapshot: PullRequestSnapshot;
    try {
      snapshot = await loadSnapshot(prUrl);
    } catch (error) {
      setState({ kind: "error", message: errorMessage(error) });
      return;
    }
    // Show the snapshot right away; the model step can take a minute.
    const idle: VerdictsState = { kind: "idle" };
    setState({ kind: "loaded", snapshot, claims: { kind: "loading" }, verdicts: idle });
    let found: ClaimsResponse;
    try {
      found = await loadClaims(prUrl, chosenProvider);
    } catch (error) {
      setState({
        kind: "loaded",
        snapshot,
        claims: { kind: "error", message: errorMessage(error) },
        verdicts: idle,
      });
      return;
    }
    // The claims were checked against this snapshot's text, so show the two together.
    const claims: ClaimsState = { kind: "loaded", claims: found.claims };
    if (found.claims.claims.length === 0) {
      setState({ kind: "loaded", snapshot: found.snapshot, claims, verdicts: idle });
      return;
    }
    setState({ kind: "loaded", snapshot: found.snapshot, claims, verdicts: { kind: "loading" } });
    try {
      const checked = await loadVerdicts(prUrl, found.claims.claims, chosenProvider);
      // Every citation refers to the commit the verdicts were checked at, so show that snapshot.
      setState({
        kind: "loaded",
        snapshot: checked.snapshot,
        claims,
        verdicts: { kind: "loaded", verdicts: checked.verdicts, repo: checked.snapshot.ref },
      });
    } catch (error) {
      setState({
        kind: "loaded",
        snapshot: found.snapshot,
        claims,
        verdicts: { kind: "error", message: errorMessage(error) },
      });
    }
  }

  const busy =
    state.kind === "loading" ||
    (state.kind === "loaded" &&
      (state.claims.kind === "loading" || state.verdicts.kind === "loading"));

  return (
    <main>
      <h1>PR Evidence Reviewer</h1>
      <p className="lede">
        Checks what a pull request says it does against its diff, tests, and CI. You make the
        approval decision.
      </p>
      <PrLinkForm
        busy={busy}
        onSubmit={(prUrl) => {
          void load(prUrl);
        }}
      />
      <ProviderSelect
        state={providers}
        selected={providerId}
        disabled={busy}
        onChange={setProviderId}
      />
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement(state)}
      </p>
      {state.kind === "error" && (
        <div className="error" role="alert">
          {state.message}
        </div>
      )}
      {state.kind === "loaded" && (
        <>
          <SnapshotView snapshot={state.snapshot} />
          <ClaimsView
            state={state.claims}
            verdicts={state.verdicts}
            providerName={
              state.claims.kind === "loaded"
                ? providerName(providers, state.claims.claims.provider)
                : null
            }
            verdictsProviderName={
              state.verdicts.kind === "loaded"
                ? providerName(providers, state.verdicts.verdicts.provider)
                : null
            }
          />
        </>
      )}
    </main>
  );
}
