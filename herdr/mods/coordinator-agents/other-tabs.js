import { div, svg } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { agents, panes, spaces, focused, run } from "herdr";
import { icon } from "./icon.js";
import { leadPanes, title } from "./tab-names.js";

export const OTHER_TABS_GROUP = "other-tabs";

// Tabs with no agent, in space order: they have no row in the agents list.
// Two in one space that would read the same get their position added.
// A settled tab (put away on this screen) is left out unless it is the shown one.
export function otherTabs() {
  const shownTab = focused()?.tab;
  const agentTabs = new Set(agents().map((a) => a.tab));
  const leads = leadPanes(panes());
  const found = spaces().flatMap((space) => space.tabs
    .filter((tab) => !agentTabs.has(tab.id) && (!tab.settled || tab.id === shownTab))
    .map((tab) => {
      const lead = leads.get(tab.id);
      const named = title(tab, lead);
      const folder = lead?.cwd?.replace(/\/+$/, "").split("/").pop();
      const name = /^\d+$/.test(named) ? [lead?.program?.name, folder].filter(Boolean).join(" · ") || named : named;
      return { other: true, status: null, address: tab.id, tab, space, lead, name };
    }));
  const readings = Map.groupBy(found, (o) => `${o.space.id}\u0000${o.name}`);
  return found.map((o) => (readings.get(`${o.space.id}\u0000${o.name}`).length > 1 ? { ...o, name: `${o.name} #${o.tab.index}` } : o));
}

// Splits the tabs: those of a pinned agent's space go under its first pinned agent, the rest stay for one "Other tabs" group.
export function splitTabs(tabs, pinned) {
  const owner = new Map();
  for (const a of pinned) if (!owner.has(a.space)) owner.set(a.space, a.address);
  const ownedBy = Map.groupBy(tabs.filter((o) => owner.has(o.space.id)), (o) => owner.get(o.space.id));
  return { ownedBy, rest: tabs.filter((o) => !owner.has(o.space.id)) };
}

// The tree lines in front of a nested row.
export const treeLines = (t, lines) => lines.map((l) => div().self_start().mt(-4).w(9).h(17).mr(4).flex_none()
  .when(Boolean(l), (el) => el.border_l(1.5).border_color(t.input))
  .when(l === "└" || l === "├", (el) => el.border_b(1.5).rounded_bl(6)));

// Sub-agent lines for a tree that goes on with a sibling after its last sub-agent.
export function continued(subs) {
  const last = subs.findLastIndex((s) => s.lines.length === 1);
  const open = (l) => (l === "└" ? "├" : l === "" ? "│" : l);
  return subs.map((s, i) => (i < last ? s : { ...s, lines: [open(s.lines[0]), ...s.lines.slice(1)] }));
}

const nestedRow = (id, lines) => h_flex().id(id).pl(39).pr(8).h(26).flex_none().rounded(8).items_center().text_size(13.5)
  .cursor_pointer().children(lines);

// A tab with no agent: its program's icon (a terminal for a shell) and its name, dimmer than an agent.
// With `lines`, it hangs under a pinned agent like a sub-agent.
export function tabRow(t, o, { lines = null, showSpace = false } = {}) {
  const { tab, space, lead, name } = o;
  const on = tab.id === focused()?.tab;
  const size = lines ? 14 : 16;
  const pic = (lead?.program?.shell ? null : icon(lead?.program, size, t.muted, true)) ?? svg("mods/workspace-chrome/terminal.svg").size(size).flex_none().text_color(t.dim);
  const machine = lines ? null : space.machine;
  const label = v_flex().flex_1().min_w(0).justify_center()
    .child(div().truncate().text_color(on ? t.foreground : t.muted).child(name))
    .when(Boolean(machine), (el) => el.child(div().mt(-3).text_size(9.5).text_color(t.dim).truncate().child(machine)));
  return (lines ? nestedRow(`tab-${tab.id}`, treeLines(t, lines)).child(div().mr(8).child(pic)) : h_flex().id(`tab-${tab.id}`).px(8).h(32).flex_none().rounded(8).items_center().gap(9).text_size(14).cursor_pointer().child(pic))
    .when(on, (el) => el.bg(t.selected))
    .hover((s) => s.bg(t.hover))
    .on_click(() => (lead ? run("pane.focus", { pane: lead.pane }) : run("tab.select", { id: tab.id })))
    .child(label)
    .when(showSpace, (el) => el.child(div().flex_shrink(1).min_w(0).max_w(80).truncate().text_size(9.5).text_color(t.dim).child(space.name)));
}

// Under a pinned agent: a folded "n tabs" row that opens to that space's tabs.
export function tabsSection(t, tabs, open, toggle) {
  const toggleRow = nestedRow(`tabs-${tabs[0].space.id}`, treeLines(t, ["└"]))
    .hover((s) => s.bg(t.hover))
    .on_click(toggle)
    .child(div().mr(8).child(svg(`mods/workspace-chrome/chevron-${open ? "down" : "right"}.svg`).size(14).flex_none().text_color(t.muted)))
    .child(div().text_color(t.muted).child(tabs.length === 1 ? "1 tab" : `${tabs.length} tabs`));
  return [toggleRow, ...(open ? tabs.map((o, i) => tabRow(t, o, { lines: ["", i === tabs.length - 1 ? "└" : "├"] })) : [])];
}
