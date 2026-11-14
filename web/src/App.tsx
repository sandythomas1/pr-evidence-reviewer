import { useState } from "react";
import { ApiError, fetchSnapshot } from "./api";
import { PrLinkForm } from "./components/PrLinkForm";
import { SnapshotView } from "./components/SnapshotView";
import type { PullRequestSnapshot } from "./types";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; snapshot: PullRequestSnapshot };

interface Props {
  loadSnapshot?: (prUrl: string) => Promise<PullRequestSnapshot>;
}

function announcement(state: State): string {
  switch (state.kind) {
    case "loading":
      return "Loading the pull request from GitHub…";
    case "loaded": {
      const { owner, repo, number } = state.snapshot.ref;
      return `Loaded ${owner}/${repo} #${String(number)}.`;
    }
    default:
      return "";
  }
}

export function App({ loadSnapshot = fetchSnapshot }: Props) {
  const [state, setState] = useState<State>({ kind: "idle" });

  async function load(prUrl: string) {
    setState({ kind: "loading" });
    try {
      setState({ kind: "loaded", snapshot: await loadSnapshot(prUrl) });
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : "Something went wrong. Try again.";
      setState({ kind: "error", message });
    }
  }

  return (
    <main>
      <h1>PR Evidence Reviewer</h1>
      <p className="lede">
        Checks what a pull request says it does against its diff, tests, and CI. You make the
        approval decision.
      </p>
      <PrLinkForm
        busy={state.kind === "loading"}
        onSubmit={(prUrl) => {
          void load(prUrl);
        }}
      />
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement(state)}
      </p>
      {state.kind === "error" && (
        <div className="error" role="alert">
          {state.message}
        </div>
      )}
      {state.kind === "loaded" && <SnapshotView snapshot={state.snapshot} />}
    </main>
  );
}
