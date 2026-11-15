// Segmented: a small group of <button aria-pressed>, one pressed, for a filter with a few choices. It replaces the
// native <select> of §5.3: WebKit (Playwright's 27.2 build) reports a style-src-attr CSP violation for every <select>
// element it creates (its user-agent shadow tree carries an inline style), even one made by plain DOM calls, and
// D29 allows no violation. No overlay, no portal, no scroll lock.
export function Segmented<T extends string>({ label, value, options, onChange, testid }: { label: string; value: T; options: { v: T; text: string }[]; onChange: (v: T) => void; testid?: string }) {
  return (
    <div className="flex items-center gap-1.5 text-xs text-text-muted">
      <span id={`${testid}-label`}>{label}</span>
      <div role="group" aria-labelledby={`${testid}-label`} data-testid={testid} className="flex items-center rounded-md border border-border bg-surface p-0.5">
        {options.map((o) => {
          const on = o.v === value;
          return (
            <button
              key={o.v}
              type="button"
              aria-pressed={on}
              onClick={() => onChange(o.v)}
              data-value={o.v}
              className={`rounded-sm px-2 py-0.5 text-xs ${on ? "bg-surface-raised font-medium text-text ring-1 ring-inset ring-border-strong" : "text-text-muted hover:text-text"}`}
            >
              {o.text}
            </button>
          );
        })}
      </div>
    </div>
  );
}
