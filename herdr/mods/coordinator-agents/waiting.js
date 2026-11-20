// Claude Code reports `working` while it sits at its prompt with background commands still
// running, with the message "N shells still running", and reports each command as a record
// keyed by its task ID. A command that only waits until it is stopped, such as the fleet
// watcher, declares itself with a `fleet.waiting.<task ID>` label on its agent's terminal.
const WAITING_LABEL_PREFIX = "fleet.waiting.";
const AT_PROMPT_WITH_SHELLS = /^(\d+) shells? still running$/;

// The agent sits at its prompt, and every background command it runs declared itself waiting.
// A label left by a killed command names a task with no running record, so it hides nothing.
export function onlyWaiting(terminal) {
  const report = terminal?.report;
  const message = typeof report === "string" ? report : report?.msg;
  const shells = Number(AT_PROMPT_WITH_SHELLS.exec(message ?? "")?.[1] ?? 0);
  const running = Object.entries(terminal?.records ?? {}).filter(([, record]) => record?.state === "working").map(([id]) => id);
  const labels = terminal?.labels ?? {};
  const everyShellHasARecord = shells > 0 && running.length >= shells;
  return everyShellHasARecord && running.every((id) => Boolean(labels[WAITING_LABEL_PREFIX + id]));
}

export const waitingAsIdle = (status, terminal) => (status === "working" && onlyWaiting(terminal) ? "idle" : status);

// agents() with each declared waiter's status turned to idle; `all` is terminals().
export function agentsWithWaitingAsIdle(list, all) {
  const terminalOf = new Map(all.map((t) => [t.address, t]));
  return list.map((a) => {
    const status = waitingAsIdle(a.status, terminalOf.get(a.address));
    return status === a.status ? a : { ...a, status };
  });
}
