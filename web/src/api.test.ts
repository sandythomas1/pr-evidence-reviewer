import { ApiError, fetchSnapshot } from "./api";
import { makeSnapshot } from "./test/fixtures";

const LINK = "https://github.com/octo-org/widgets/pull/42";

function respond(status: number, body: unknown): typeof fetch {
  return vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status })));
}

describe("fetchSnapshot", () => {
  it("posts the link to the runner and returns the snapshot", async () => {
    const snapshot = makeSnapshot();
    const fetchImpl = respond(200, snapshot);

    await expect(fetchSnapshot(LINK, fetchImpl)).resolves.toEqual(snapshot);
    expect(fetchImpl).toHaveBeenCalledWith("/api/snapshots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pr_url: LINK }),
    });
  });

  it("surfaces the runner's error message and code", async () => {
    const fetchImpl = respond(404, {
      error: { code: "github_not_found", message: "Couldn't find octo-org/widgets#42." },
    });

    const error: unknown = await fetchSnapshot(LINK, fetchImpl).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("Couldn't find octo-org/widgets#42.");
    expect((error as ApiError).code).toBe("github_not_found");
  });

  it("falls back to a generic message when the error body isn't the envelope", async () => {
    await expect(fetchSnapshot(LINK, respond(500, "<html>"))).rejects.toThrow(
      "The runner returned an error (500). Try again.",
    );
  });

  it("explains when the runner can't be reached", async () => {
    const fetchImpl: typeof fetch = () => Promise.reject(new TypeError("Failed to fetch"));

    await expect(fetchSnapshot(LINK, fetchImpl)).rejects.toThrow(/prev serve/);
  });

  it("rejects a success reply that isn't JSON", async () => {
    const fetchImpl: typeof fetch = () => Promise.resolve(new Response("not json"));

    await expect(fetchSnapshot(LINK, fetchImpl)).rejects.toThrow(/couldn't be read/);
  });
});
