// The sidebar's top row without Herdr's logo and name: search and the sidebar toggle.
// The icons are the shipped workspace-chrome mod's.
// Once a day it reads LAYOUT_VERSION from the layout's main branch. When that is newer, a dot left
// of search opens a popup with the line to paste to an agent.

import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { theme, run, window, shared, setShared, layout, checking, props, openOverlay, closeOverlay } from "herdr";

// One more in each pull request that changes the layout for people who have it installed.
// Installs read it from this line on GitHub; RECIPE.md's "Updating" finds files by it.
const LAYOUT_VERSION = 1;
// false: never contact GitHub and never show that an update exists.
const CHECK_FOR_UPDATES = true;
const LATEST_URL = "https://raw.githubusercontent.com/petekp/coordinator/main/herdr/mods/coordinator-topbar/main.js";
const LATEST_KEY = "coordinator.latest";
const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

// Shared, so restarts and other windows skip a check made in the last day. A failed check
// leaves the key alone, so the next hourly tick tries again.
async function checkForUpdate() {
  const last = shared(LATEST_KEY);
  if (last && Date.now() - last.checkedAt < DAY_MS) return;
  try {
    const response = await fetch(LATEST_URL);
    if (!response.ok) return;
    const found = (await response.text()).match(/^const LAYOUT_VERSION = (\d+);$/m);
    if (found) setShared(LATEST_KEY, { checkedAt: Date.now(), version: Number(found[1]) });
  } catch {}
}

const isUpdateAvailable = () => CHECK_FOR_UPDATES && (shared(LATEST_KEY)?.version ?? 0) > LAYOUT_VERSION;

function find(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children ?? []) {
    const found = find(child, id);
    if (found) return found;
  }
  return null;
}

// The sidebar's rail goes by its width, not a flag: dragging its edge narrows it too.
const narrow = () => (find(layout(), "sidebar")?.size?.px ?? Infinity) < 160;

// To the rail (on macOS wide enough for the traffic lights), or back to the width it had.
function toggleSidebar() {
  const width = find(layout(), "sidebar")?.size?.px;
  if (!narrow()) setShared("sidebar.width", width);
  // Mods edited before the rail went by width still read the flag.
  setShared("sidebar.collapsed", !narrow());
  run("layout.resize", { id: "sidebar", px: narrow() ? (shared("sidebar.width") ?? 268) : window()?.platform === "macos" ? 76 : 44 });
}

const UPDATE_PROMPT = "Update the Coordinator layout from herdr.dev/layouts/petekp/coordinator--herdr";
const openUpdatePopup = () => openOverlay({ mod: "coordinator-topbar", kind: "popup", at: "pointer", width: 340, height: 206, props: { updatePopup: true } });

function updatePopup(t) {
  const latest = shared(LATEST_KEY)?.version;
  return v_flex().size_full().bg(t.popover).p(14).gap(8).text_size(13).text_color(t.foreground)
    .child(div().font_semibold().child(`Coordinator layout version ${latest} is available`))
    .child(div().text_color(t.muted).child(`You have version ${LAYOUT_VERSION}. To update, paste this to your agent:`))
    .child(div().p(8).rounded(6).bg(t.input).text_size(12).child(UPDATE_PROMPT))
    .child(h_flex().justify_end().child(div().id("copy").px(10).py(4).rounded(6).bg(t.solid).text_color(t.solid_foreground)
      .on_click((_e, cx) => { cx.write_to_clipboard(UPDATE_PROMPT); closeOverlay(props().overlay); }).child("Copy")));
}

function updateMarker(t) {
  return div().id("update").size(26).flex_none().flex().items_center().justify_center().rounded(6).cursor_pointer().hover(s => s.bg(t.hover))
    .on_mouse_down("left", (_e, cx) => cx.stop_propagation()).on_click(openUpdatePopup)
    .child(div().size(8).rounded(4).bg(t.accent));
}

export default class Topbar extends View {
  init(_props, cx) {
    const isPopup = Boolean(props()?.overlay);
    if (checking() || isPopup || !CHECK_FOR_UPDATES) return;
    checkForUpdate();
    cx.timer.every(HOUR_MS, () => checkForUpdate());
  }
  button(id, label, action) {
    const t = theme();
    return div().id(id).size(26).flex_none().flex().items_center().justify_center().rounded(6).text_color(t.muted).hover(s => s.bg(t.hover).text_color(t.foreground))
      .on_mouse_down("left", (_e, cx) => cx.stop_propagation()).on_click(action).child(label);
  }
  render() {
    const t = theme();
    if (props()?.updatePopup) return updatePopup(t);
    const collapsed = narrow();
    return h_flex().size_full().bg(t.chrome).pl(collapsed ? 6 : 14).pr(10).gap(4).items_center().text_size(13.5)
      .when(window()?.platform === "macos" && !window()?.fullscreen && !collapsed, el => el.pl(76))
      .when(!collapsed, el => el.child(div().flex_1()))
      // On the rail the marker waits until the sidebar is widened.
      .when(!collapsed && isUpdateAvailable(), el => el.child(updateMarker(t)))
      .when(!collapsed, el => el.child(this.button("search", svg("mods/workspace-chrome/search.svg").size(15), () => run("mod.open", { name: "picker" }))))
      // On the macOS rail the traffic lights fill the row; the agents list's » unfolds it.
      .when(!(collapsed && window()?.platform === "macos"), el => el.child(this.button("sidebar-toggle", svg("mods/workspace-chrome/sidebar.svg").size(15), toggleSidebar)));
  }
}
