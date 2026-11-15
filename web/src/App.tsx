import { useEffect, useState } from "react";
import { ApiError, fetchClaims, fetchProviders, fetchSnapshot } from "./api";
import { ClaimsView, type ClaimsState } from "./components/ClaimsView";
import { PrLinkForm } from "./components/PrLinkForm";
import { defaultProvider, ProviderSelect, type ProvidersState } from "./components/ProviderSelect";
import { SnapshotView } from "./components/SnapshotView";
import type { ClaimsResponse, ProviderStatus, PullRequestSnapshot } from "./types";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; snapshot: PullRequestSnapshot; claims: ClaimsState };

interface Props {
  loadSnapshot?: (prUrl: string) => Promise<PullRequestSnapshot>;
  loadClaims?: (prUrl: string, providerId: string | null) => Promise<ClaimsResponse>;
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
      return `Loaded ${name}. Found ${String(count)} ${count === 1 ? "claim" : "claims"}.`;
    }
    default:
      return "";
  }
}

function providerName(providers: ProvidersState, providerId: string | null): string | null {
  if (providers.kind !== "loaded" || providerId === null) return null;
  return providers.providers.find((provider) => provider.id === providerId)?.name ?? null;
}

export function App({
  loadSnapshot = fetchSnapshot,
  loadClaims = fetchClaims,
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
    setState({ kind: "loading" });
    let snapshot: PullRequestSnapshot;
    try {
      snapshot = await loadSnapshot(prUrl);
    } catch (error) {
      setState({ kind: "error", message: errorMessage(error) });
      return;
    }
    // Show the snapshot right away; the model step can take a minute.
    setState({ kind: "loaded", snapshot, claims: { kind: "loading" } });
    try {
      const result = await loadClaims(prUrl, providerId);
      // The claims were checked against this snapshot's text, so show the two together.
      setState({
        kind: "loaded",
        snapshot: result.snapshot,
        claims: { kind: "loaded", claims: result.claims },
      });
    } catch (error) {
      setState({
        kind: "loaded",
        snapshot,
        claims: { kind: "error", message: errorMessage(error) },
      });
    }
  }

  const busy =
    state.kind === "loading" || (state.kind === "loaded" && state.claims.kind === "loading");

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
            providerName={
              state.claims.kind === "loaded"
                ? providerName(providers, state.claims.claims.provider)
                : null
            }
          />
        </>
      )}
    </main>
  );
}
