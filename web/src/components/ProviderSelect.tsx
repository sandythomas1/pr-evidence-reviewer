import { useId } from "react";
import type { ProviderStatus } from "../types";

export type ProvidersState =
  { kind: "loading" } | { kind: "error" } | { kind: "loaded"; providers: ProviderStatus[] };

interface Props {
  state: ProvidersState;
  selected: string | null;
  disabled: boolean;
  onChange: (providerId: string) => void;
}

/** The first ready provider, which is what the runner uses when nothing is chosen. */
export function defaultProvider(providers: ProviderStatus[]): string | null {
  return providers.find((provider) => provider.available)?.id ?? null;
}

export function ProviderSelect({ state, selected, disabled, onChange }: Props) {
  const selectId = useId();
  const detailId = useId();
  const reasonsId = useId();

  if (state.kind === "loading") {
    return <p className="hint provider-field">Checking which model providers are ready…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="hint provider-field">
        Couldn&apos;t check which model providers are ready, so the first ready one will be used.
      </p>
    );
  }

  const { providers } = state;
  const chosen = providers.find((provider) => provider.id === selected);
  const notReady = providers.filter((provider) => !provider.available);
  const describedBy = [chosen ? detailId : null, notReady.length > 0 ? reasonsId : null]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="provider-field">
      <label htmlFor={selectId}>Model provider</label>
      <select
        id={selectId}
        value={selected ?? ""}
        disabled={disabled}
        aria-describedby={describedBy || undefined}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      >
        {!chosen && (
          <option value="" disabled>
            No model provider is ready
          </option>
        )}
        {providers.map((provider) => (
          <option key={provider.id} value={provider.id} disabled={!provider.available}>
            {provider.available ? provider.name : `${provider.name} (not ready)`}
          </option>
        ))}
      </select>
      {chosen && (
        <p id={detailId} className="hint">
          {chosen.detail}
        </p>
      )}
      {notReady.length > 0 && (
        <div id={reasonsId} className="provider-reasons">
          <p className="hint">Not ready:</p>
          <ul>
            {notReady.map((provider) => (
              <li key={provider.id}>
                <strong>{provider.name}:</strong> {provider.detail}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
