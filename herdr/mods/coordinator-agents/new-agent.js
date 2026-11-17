// The menu that starts an agent: a space, then a program. `view` is the mod instance that holds it.

import { div, svg } from "gpui-kit";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { spaces, focused, run, openOverlay, closeOverlay, props, knownAgents } from "herdr";
import { spaceTile } from "./tile.js";
import { icon } from "./icon.js";

const MAX_SPACES = 8;
const ROW_HEIGHT = 31;
const SECTION_HEIGHT = 26;
const EMPTY_NOTE_HEIGHT = 26;
const NAME_FIELD_HEIGHT = 38;

// Only spaces on the machine the window runs on: a null machine.
const localSpaces = () => spaces().filter((s) => !s.machine).slice(0, MAX_SPACES);

async function installedPrograms() {
  try {
    return (await knownAgents()).filter((p) => p.installed);
  } catch {
    return [];
  }
}

// Asked ahead of the first click, so the popup usually opens at once.
export async function loadPrograms(view) {
  const programs = await installedPrograms();
  if (programs.length) view.programs = programs;
}

// The shown space is checked first; if it is on another machine, the first local one is.
export function initNewMenu(view, cx) {
  view.name = InputState.new({ placeholder: "Name (optional)" });
  view.name.focus();
  cx.timer.after(600, () => view.name.focus());
  const local = localSpaces();
  view.space = (local.find((s) => s.id === focused()?.space) ?? local.find((s) => s.active) ?? local[0])?.id;
}

// The popup's size is fixed when it opens, so it opens once the programs are known.
export async function openNewMenu(view) {
  const programs = view.programs ?? await installedPrograms();
  if (programs.length) view.programs = programs;
  const programsHeight = programs.length ? programs.length * ROW_HEIGHT : EMPTY_NOTE_HEIGHT;
  const height = 20 + NAME_FIELD_HEIGHT + 2 * SECTION_HEIGHT + localSpaces().length * ROW_HEIGHT + programsHeight;
  openOverlay({ mod: "coordinator-agents", kind: "popup", at: "pointer", width: 220, height, props: { newMenu: { programs } } });
}

export function newMenu(t, view) {
  const { programs } = props().newMenu;
  const item = (id, lead, label, on, click) => h_flex()
    .id(id).h(ROW_HEIGHT).px(8).gap(9).rounded(7).items_center().text_color(t.foreground)
    .hover((el) => el.bg(t.hover))
    .on_click(click)
    .child(lead)
    .child(div().flex_1().min_w(0).truncate().child(label))
    .when(on, (el) => el.child(svg("mods/workspace-chrome/check.svg").size(14).flex_none().text_color(t.foreground)));
  const heading = (text) => div().px(8).pt(4).pb(6).text_size(11).text_color(t.dim).child(text);
  const start = (program) => {
    const name = view.name.value().trim() || undefined;
    run("tab.new", { name, space: view.space, folder: spaces().find((s) => s.id === view.space)?.folder || undefined, argv: program.run });
    closeOverlay(props().overlay);
  };
  return v_flex().w_full().p(5).bg(t.popover).text_size(12.8)
    .child(h_flex().h(30).mx(3).mb(8).flex_none().px(8).items_center().rounded(7).border_1().border_color(t.accent).bg(t.background)
      .child(Input.new(view.name).flex_1().h_full().text_size(12.8).text_color(t.foreground)))
    .child(heading("In space"))
    .children(localSpaces().map((s) => item(`new-space-${s.id}`, spaceTile(t, { id: s.id, name: s.name }), s.name, s.id === view.space, (_e, cx) => {
      view.space = s.id;
      cx.notify();
    })))
    .child(div().mx(3).my(4).h(1).bg(t.border))
    .child(heading("Start"))
    .children(programs.length ? programs.map((p) => item(`new-${p.key}`, icon(p, 16, t.muted) ?? div().size(16), p.name, false, () => start(p))) : [div().px(8).text_size(12).text_color(t.dim).child("No agent programs found.")]);
}

export const headerButton = (t, view) => div().id("ag-new").text_color(t.dim)
  .hover((s) => s.text_color(t.foreground))
  .on_click(() => openNewMenu(view))
  .tooltip("New agent")
  .child(svg("mods/workspace-chrome/plus.svg").size(14).flex_none());
