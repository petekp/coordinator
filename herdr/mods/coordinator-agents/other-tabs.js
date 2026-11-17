import { div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { agents, panes, spaces, focused, run } from "herdr";
import { icon } from "./icon.js";
import { leadPanes, title } from "./tab-names.js";

// How tabs without an agent are listed: "groups", "folded" or "unfold".
export const OTHER_TABS = "folded";
export const OTHER_TABS_GROUP = "other-tabs";

// Tabs with no agent, in space order: they have no row in the agents list.
export function otherTabs() {
  const agentTabs = new Set(agents().map((a) => a.tab));
  const leads = leadPanes(panes());
  return spaces().flatMap((s) => s.tabs
    .filter((tab) => !agentTabs.has(tab.id))
    .map((tab) => ({ other: true, status: null, address: tab.id, tab, space: s, lead: leads.get(tab.id) })));
}

// A tab with no agent: its program's icon (a terminal for a shell) and its name, dimmer than an agent.
export function tabRow(t, o, { nested = false, showSpace = false } = {}) {
  const { tab, space, lead } = o;
  const named = title(tab, lead);
  const where = lead?.cwd?.replace(/\/+$/, "").split("/").pop();
  const name = /^\d+$/.test(named) ? [lead?.program?.name, where].filter(Boolean).join(" \u00b7 ") || named : named;
  const on = tab.id === focused()?.tab;
  const size = nested ? 14 : 16;
  const pic = (lead?.program?.shell ? null : icon(lead?.program, size, t.muted, true)) ?? svg("mods/workspace-chrome/terminal.svg").size(size).flex_none().text_color(t.dim);
  return h_flex()
    .id(`tab-${tab.id}`)
    .when(nested, (el) => el.pl(39).pr(8).h(26).text_size(13.5))
    .when(!nested, (el) => el.px(8).h(32).text_size(14))
    .flex_none().rounded(8).items_center().gap(9)
    .when(on, (el) => el.bg(t.selected))
    .cursor_pointer()
    .hover((s) => s.bg(t.hover))
    .on_click(() => (lead ? run("pane.focus", { pane: lead.pane }) : run("tab.select", { id: tab.id })))
    .child(pic)
    .child(div().flex_1().min_w(0).truncate().text_color(on ? t.foreground : t.muted).child(name))
    .when(showSpace, (el) => el.child(div().flex_shrink(1).min_w(0).max_w(80).truncate().text_size(9.5).text_color(t.dim).child(space.name)));
}

// Adds the tabs to the group list: into their space's group, or as one "Other tabs" group.
export function addOtherTabGroups(groups, others, bySpace, byPriority) {
  if (OTHER_TABS === "groups" && !byPriority) {
    for (const [space, tabs] of bySpace) {
      const group = groups.find((g) => g.space === space);
      if (group) group.members.push(...tabs);
      else groups.push({ id: `sp-${space}`, space, label: tabs[0].space.name, status: null, members: tabs });
    }
  } else if (OTHER_TABS !== "unfold" && others.length) {
    groups.push({ id: OTHER_TABS_GROUP, label: "Other tabs", status: null, members: others, showSpace: true });
  }
}
