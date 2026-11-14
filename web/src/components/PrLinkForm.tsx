import { useId, useState, type SubmitEvent } from "react";
import { prLinkProblem } from "../prUrl";

interface Props {
  busy: boolean;
  onSubmit: (prUrl: string) => void;
}

export function PrLinkForm({ busy, onSubmit }: Props) {
  const inputId = useId();
  const hintId = useId();
  const problemId = useId();
  const [value, setValue] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = prLinkProblem(value);
    setProblem(found);
    if (!found) onSubmit(value.trim());
  }

  return (
    <form className="pr-form" onSubmit={handleSubmit} noValidate>
      <label htmlFor={inputId}>Pull request link</label>
      <p id={hintId} className="hint">
        A github.com pull request your gh sign-in can read. Nothing is posted to GitHub.
      </p>
      <div className="pr-form-row">
        <input
          id={inputId}
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="https://github.com/owner/repo/pull/123"
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
          }}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${hintId} ${problemId}` : hintId}
        />
        <button type="submit" disabled={busy}>
          {busy ? "Loading…" : "Load pull request"}
        </button>
      </div>
      {problem && (
        <p id={problemId} className="field-error">
          {problem}
        </p>
      )}
    </form>
  );
}
