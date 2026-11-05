/** Small, decorative diagrams of the work each starter helps with. */
export function TaskIllustration({ variant }: { variant: "explore" | "implement" | "investigate" }) {
  return <svg className={`workbench-illustration workbench-illustration--${variant}`} viewBox="0 0 240 88" fill="none" aria-hidden="true" focusable="false">
    {variant === "explore" ? <>
      <path className="illustration-link" d="M91 44h28m0-22v44m0-44h22m-22 22h22m-22 22h22" />
      <rect className="illustration-paper" x="31" y="18" width="60" height="52" rx="10" />
      <path className="illustration-ink" d="M47 34h11l4 5h14v16H47z" />
      <rect className="illustration-paper" x="141" y="10" width="65" height="24" rx="6" />
      <rect className="illustration-paper" x="141" y="54" width="65" height="24" rx="6" />
      <rect className="illustration-fill" x="141" y="36" width="49" height="16" rx="5" />
      <path className="illustration-muted" d="M154 22h25m-25 44h37" />
      <circle className="illustration-dot" cx="119" cy="44" r="3" />
    </> : variant === "implement" ? <>
      <rect className="illustration-paper" x="36" y="11" width="168" height="66" rx="9" />
      <path className="illustration-muted" d="M36 29h168" />
      <circle className="illustration-dot" cx="49" cy="20" r="2" />
      <circle className="illustration-dot" cx="57" cy="20" r="2" opacity=".45" />
      <circle className="illustration-dot" cx="65" cy="20" r="2" opacity=".2" />
      <path className="illustration-ink" d="m63 42-9 9 9 9m22-18 9 9-9 9m-7-21-8 24" />
      <rect className="illustration-fill" x="112" y="38" width="75" height="10" rx="3" />
      <path className="illustration-muted" d="M112 57h38m-38 8h61" />
    </> : <>
      <rect className="illustration-paper" x="43" y="11" width="97" height="65" rx="9" />
      <path className="illustration-muted" d="M57 26h65M57 36h41m-41 20h51m-51 10h66" />
      <rect className="illustration-fill" x="53" y="41" width="68" height="8" rx="3" />
      <circle className="illustration-paper" cx="158" cy="39" r="24" />
      <path className="illustration-ink" d="m176 57 20 20m-46-37 6 6 11-13" />
      <path className="illustration-link" d="M128 45h-7" />
      <circle className="illustration-dot" cx="119" cy="45" r="3" />
    </>}
  </svg>;
}

/** A capability diagram, never a live task-progress indicator. */
export function HarnessDiagram() {
  return <span className="harness-diagram" aria-hidden="true">
    <svg viewBox="0 0 240 68" fill="none" focusable="false">
      <path className="illustration-link" d="M48 26h21m-4-4 4 4-4 4 M108 26h21m-4-4 4 4-4 4 M168 26h21m-4-4 4 4-4 4" />
      <path className="illustration-link" strokeDasharray="3 3" d="M210 44v14H90V46" />
      <path className="illustration-ink" d="m86 50 4-5 4 5" />
      <rect className="illustration-paper" x="12" y="8" width="36" height="36" rx="10" />
      <path className="illustration-ink" d="M23 16h9l5 5v15H23zm9 0v6h5m-10 5h6m-6 4h6" />
      <rect className="illustration-fill" x="72" y="8" width="36" height="36" rx="10" />
      <circle className="illustration-ink" cx="88" cy="24" r="7" />
      <path className="illustration-ink" d="m93 29 6 6" />
      <rect className="illustration-paper" x="132" y="8" width="36" height="36" rx="10" />
      <path className="illustration-ink" d="m144 21-5 5 5 5m12-10 5 5-5 5m-4-13-4 16" />
      <rect className="illustration-paper" x="192" y="8" width="36" height="36" rx="10" />
      <path className="illustration-ink" d="m210 16 9 4v7c0 5-9 10-9 10s-9-5-9-10v-7zm-4 10 3 3 5-6" />
    </svg>
    <span className="harness-diagram-labels"><span>目标</span><span>分析</span><span>执行</span><span>验收</span></span>
  </span>;
}
