// The menu that starts an agent: a space, then a program. `view` is the mod instance that holds it.

import { div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { spaces, focused, run, openOverlay, closeOverlay, props, knownAgents } from "herdr";
import { spaceTile } from "./tile.js";
import { icon } from "./icon.js";

// Where the control for a new agent sits: "header", "group" or "row".
export const NEW_AGENT = "header";
const MAX_SPACES = 8;
const ROW_HEIGHT = 31;

export function loadPrograms(view, cx) {
  knownAgents().then((list) => {
    view.programs = list.filter((p) => p.installed);
    cx.notify();
  }, () => {});
}

export function initNewMenu(view, cx) {
  view.space = props().newMenu.space ?? focused()?.space ?? spaces().find((s) => s.active)?.id;
  loadPrograms(view, cx);
}

// `space`: the space to start in, or null to let the menu ask.
export function openNewMenu(view, space) {
  const spaceRows = space ? 0 : 1 + Math.min(spaces().length, MAX_SPACES);
  const height = 12 + spaceRows * ROW_HEIGHT + 26 + (view.programs?.length || 3) * ROW_HEIGHT;
  openOverlay({ mod: "coordinator-agents", kind: "popup", at: "pointer", width: 220, height, props: { newMenu: { space: space ?? null } } });
}

export function newMenu(t, view) {
  const fixed = Boolean(props().newMenu.space);
  const item = (id, lead, label, on, click) => h_flex()
    .id(id).h(ROW_HEIGHT).px(8).gap(9).rounded(7).items_center().text_color(t.foreground)
    .hover((el) => el.bg(t.hover))
    .on_click(click)
    .child(lead)
    .child(div().flex_1().min_w(0).truncate().child(label))
    .when(on, (el) => el.child(svg("mods/workspace-chrome/check.svg").size(14).flex_none().text_color(t.foreground)));
  const start = (program) => {
    run("tab.new", { space: view.space, folder: spaces().find((s) => s.id === view.space)?.folder || undefined, argv: program.run });
    closeOverlay(props().overlay);
  };
  return v_flex().w_full().p(5).bg(t.popover).text_size(12.8)
    .when(!fixed, (el) => el
      .child(div().px(8).pt(4).pb(6).text_size(11).text_color(t.dim).child("In space"))
      .children(spaces().slice(0, MAX_SPACES).map((s) => item(`new-space-${s.id}`, spaceTile(t, { id: s.id, name: s.name }), s.name, s.id === view.space, (_e, cx) => {
        view.space = s.id;
        cx.notify();
      })))
      .child(div().mx(3).my(4).h(1).bg(t.border)))
    .child(div().px(8).pt(4).pb(6).text_size(11).text_color(t.dim).child("Start"))
    .children((view.programs ?? []).map((p) => item(`new-${p.key}`, icon(p, 16, t.muted) ?? div().size(16), p.name, false, () => start(p))));
}

const plus = (size) => svg("mods/workspace-chrome/plus.svg").size(size).flex_none();

export const headerButton = (t, view) => div().id("ag-new").text_color(t.dim)
  .hover((s) => s.text_color(t.foreground))
  .on_click(() => openNewMenu(view, null))
  .tooltip("New agent")
  .child(plus(14));

export const groupButton = (t, view, space, id) => div().id(`new-${id}`).flex_none().p(2).rounded(5).text_color(t.dim)
  .hover((s) => s.bg(t.hover).text_color(t.foreground))
  .tooltip("New agent in this space")
  .on_click((_e, cx) => {
    cx.stop_propagation();
    openNewMenu(view, space);
  })
  .child(plus(14));

export const newRow = (t, view) => h_flex().id("ag-new-row").px(8).h(32).flex_none().rounded(8).items_center().gap(9).text_size(14).text_color(t.muted)
  .cursor_pointer().hover((s) => s.bg(t.hover).text_color(t.foreground))
  .on_click(() => openNewMenu(view, null))
  .child(plus(16))
  .child(div().child("New agent"));
