import type { ClaimsResponse, PullRequestSnapshot } from "./types";

/** An API failure whose message is safe and useful to show the user. */
export class ApiError extends Error {
  readonly code: string;

  constructor(message: string, code: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

const UNREACHABLE =
  "Couldn't reach the local runner. Check that `prev serve` is still running, then try again.";

function errorDetail(body: unknown): { code: string; message: string } | null {
  if (typeof body !== "object" || body === null || !("error" in body)) return null;
  const error = body.error;
  if (typeof error !== "object" || error === null) return null;
  const code = "code" in error && typeof error.code === "string" ? error.code : "error";
  const message = "message" in error && typeof error.message === "string" ? error.message : null;
  return message ? { code, message } : null;
}

async function postJson<T>(path: string, payload: unknown, fetchImpl: typeof fetch): Promise<T> {
  let response: Response;
  try {
    response = await fetchImpl(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new ApiError(UNREACHABLE, "runner_unreachable");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = errorDetail(body);
    throw new ApiError(
      detail?.message ?? `The runner returned an error (${String(response.status)}). Try again.`,
      detail?.code ?? "error",
    );
  }
  if (body === null) {
    throw new ApiError("The runner sent back a reply that couldn't be read.", "bad_reply");
  }
  return body as T;
}

export function fetchSnapshot(
  prUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<PullRequestSnapshot> {
  return postJson<PullRequestSnapshot>("/api/snapshots", { pr_url: prUrl }, fetchImpl);
}

/** Slower than fetchSnapshot: the runner asks the model to pull the claims out of the PR text. */
export function fetchClaims(
  prUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ClaimsResponse> {
  return postJson<ClaimsResponse>("/api/claims", { pr_url: prUrl }, fetchImpl);
}
