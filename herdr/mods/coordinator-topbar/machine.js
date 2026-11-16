// The machine the focused terminal runs on, or null when it is on the window's own computer.
// Spaces report `machine: null` for this computer.
export function remoteMachineOf(focus, spaces) {
  return spaces.find((s) => s.id === focus?.space)?.machine ?? null;
}
