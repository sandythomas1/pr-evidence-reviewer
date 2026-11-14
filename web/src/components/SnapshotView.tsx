import type { CheckResult, PullRequestSnapshot } from "../types";

const FAILED = new Set(["failure", "error", "timed_out", "action_required"]);

const shortSha = (sha: string) => sha.slice(0, 7);
const humanize = (value: string) => value.replaceAll("_", " ");

function checkOutcome(check: CheckResult): { label: string; tone: string } {
  if (check.status !== "completed") return { label: humanize(check.status), tone: "pending" };
  if (check.conclusion === null) return { label: "no result", tone: "neutral" };
  if (check.conclusion === "success") return { label: "passed", tone: "pass" };
  if (FAILED.has(check.conclusion)) return { label: humanize(check.conclusion), tone: "fail" };
  return { label: humanize(check.conclusion), tone: "neutral" };
}

export function SnapshotView({ snapshot }: { snapshot: PullRequestSnapshot }) {
  const { ref, base, head, files, checks } = snapshot;
  return (
    <article className="snapshot" aria-labelledby="snapshot-title">
      <header>
        <p className="repo">
          {ref.owner}/{ref.repo} · #{ref.number}
          {snapshot.draft && " · draft"}
        </p>
        <h2 id="snapshot-title">{snapshot.title || "(untitled pull request)"}</h2>
        <p className="commits">
          Comparing <code title={base.sha}>{shortSha(base.sha)}</code> ({base.ref}) with{" "}
          <code title={head.sha}>{shortSha(head.sha)}</code> ({head.ref}). Every finding refers to
          commit <code>{shortSha(head.sha)}</code>.
        </p>
      </header>

      {snapshot.scope_limitations.length > 0 && (
        <section aria-labelledby="gaps-title" className="gaps">
          <h3 id="gaps-title">Not inspected</h3>
          <ul>
            {snapshot.scope_limitations.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="files-title">
        <h3 id="files-title">Changed files ({snapshot.changed_files_count})</h3>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Change</th>
                <th scope="col">Lines added</th>
                <th scope="col">Lines removed</th>
              </tr>
            </thead>
            <tbody>
              {files.map((file) => (
                <tr key={file.path}>
                  <td>
                    <code>{file.path}</code>
                    {file.previous_path && (
                      <span className="hint"> (was {file.previous_path})</span>
                    )}
                  </td>
                  <td>{file.status}</td>
                  <td className="num added">+{file.additions}</td>
                  <td className="num removed">−{file.deletions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="checks-title">
        <h3 id="checks-title">CI checks at {shortSha(head.sha)}</h3>
        {checks.length === 0 ? (
          <p>No CI results were reported for this commit, so test claims rely on test source.</p>
        ) : (
          <ul className="checks">
            {checks.map((check) => {
              const outcome = checkOutcome(check);
              return (
                <li key={`${check.source}:${check.name}`}>
                  <span className={`badge ${outcome.tone}`}>{outcome.label}</span> {check.name}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </article>
  );
}
