// Agents in grouped or priority views; parents take their most urgent sub-agent's place.
// Opened with mod.open (Cmd-K), it's the agent jumper: the top-level agents, in the list's order.

import { View, div, svg } from "gpui-kit";
import { spaceTile } from "./tile.js";
import { icon } from "./icon.js";
import { leadPanes, title } from "./tab-names.js";
import { headerButton, initNewMenu, loadPrograms, newMenu } from "./new-agent.js";
import { OTHER_TABS_GROUP, continued, otherTabs, splitTabs, tabRow, tabsSection, treeLines } from "./other-tabs.js";
import { LABEL, palette, indexOf, nextIndex } from "./colors.js";
import { agentsWithWaitingAsIdle, waitingAsIdle } from "./waiting.js";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { agents, terminals, panes, focused, spaces, run, hc, theme, settings, shared, setShared, setAgentOrder, openOverlay, closeOverlay, props, window, layout } from "herdr";

// What the inbox plugin reports on a session's terminal: why the agent stopped,
// then the Asks, Finds and PRs counts. The colors are the inbox's own, for dark
// and light appearances.
const INBOX_COUNTS = [
  ["inbox.stop", "#ffa3b0", "#a5293d"],
  ["inbox.needs-you", "#ffc107", "#745417"],
  ["inbox.findings", "#cab0ff", "#7b00e8"],
  ["inbox.prs", "#8cc8c0", "#006363"],
];

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

const RANK = { blocked: 4, done: 3, working: 2, idle: 1 };
// A sub-agent without a pane isn't in agents(): its raw word, error counting as
// blocked and a declared waiter as idle. It's never done: it reports to its
// parent, and the parent's done is the news.
const kidStatus = (k, listed) => {
  const s = listed.get(k.address) ?? waitingAsIdle(k.status, k);
  return s === "error" ? "blocked" : s === "done" ? "idle" : s;
};
const SORTS = [
  { id: "priority", label: "Priority", icon: "clock" },
  { id: "grouped", label: "Grouped by space", icon: "layers" },
];
const DEFAULT_SORT_ID = "grouped";
const sortOf = () => SORTS.find((s) => s.id === (shared("agents.sort") ?? DEFAULT_SORT_ID)) ?? SORTS[0];
// Addresses of pinned agents in the order dragged, in shared settings.
const PIN_ORDER = "agents.pinned.order";
const JUMP_ROW_HEIGHT = 30;
// How far, in points, the pointer moves with the button held before a press on a pinned row becomes a drag.
const DRAG_START_DISTANCE = 4;
const JUMP_LIST_HEIGHT = 360;
// The menus are overlays too, each with its own prop.
const isJumper = () => Boolean(props()?.overlay) && !props()?.sortMenu && !props()?.agentMenu && !props()?.newMenu;
const SECTION = { 4: "Needs you", 3: "Done", 2: "Working", 1: "Idle" };
const SECTION_STATUS = { 4: "blocked", 3: "done", 2: "working" };

export default class Agents extends View {
  init(_props, cx) {
    if (props()?.newMenu) return initNewMenu(this, cx);
    if (props()?.sortMenu || props()?.agentMenu) return;
    // Each agent's last state and when it changed (ms), kept across restarts.
    try { this.changed = JSON.parse(localStorage.getItem("changed") ?? "{}"); } catch { this.changed = {}; }
    if (!this.changed || typeof this.changed !== "object" || Array.isArray(this.changed)) this.changed = {};
    if (isJumper()) return this.initJumper(cx);
    // Colors written but not yet echoed back as labels, by terminal address.
    this.pendingColors = new Map();
    this.folded = new Set([OTHER_TABS_GROUP]);
    // Pinned agents whose tabs are shown, by address.
    this.tabsOpen = new Set();
    loadPrograms(this);
    try { this.pendingPins = JSON.parse(localStorage.getItem("pins") ?? "[]"); } catch { this.pendingPins = []; }
    if (!Array.isArray(this.pendingPins)) this.pendingPins = [];
    if (!this.pendingPins.length) localStorage.removeItem("pins");
    this.migratingPins = new Set();
  }

  initJumper(cx) {
    this.filter = "";
    this.at = null;
    this.scroll = 0;
    this.handle = cx.focus_handle();
    this.input = InputState.new({ placeholder: "Go to an agent\u2026" });
    this.input.on("change", (_e, cx) => {
      this.filter = this.input.value();
      this.at = 0;
      cx.notify();
    });
    this.input.on("submit", () => this.jump(this.jumpRows(terminals())[this.at]));
    this.input.focus();
    cx.timer.after(600, () => this.input.focus());
  }

  // Hides the tab bar while the shown tab holds a pinned agent (a coordinator), and shows it otherwise.
  // The layout names the tab bar's block in the `tabBarBlock` prop; without it, nothing hides.
  syncTabBar(list) {
    const block = props()?.tabBarBlock;
    if (!block) return;
    const tab = focused()?.tab;
    const hide = Boolean(tab) && list.some((a) => a.tab === tab && a.pinned);
    if (this.tabBarHidden === hide) return;
    this.tabBarHidden = hide;
    run(hide ? "layout.hide" : "layout.show", { id: block });
  }

  // Stamps agents whose state changed. Done and idle count as one: looking at a
  // finished agent turns done into idle, and looking isn't the agent doing anything.
  track(list, now) {
    let dirty = false;
    for (const a of list) {
      const s = a.status === "done" ? "idle" : a.status;
      if (this.changed[a.address]?.s === s) continue;
      this.changed[a.address] = { s, at: now };
      dirty = true;
    }
    if (!dirty) return;
    // Gone and old: the list alone is empty right after a start.
    const listed = new Set(list.map((a) => a.address));
    for (const [k, c] of Object.entries(this.changed)) if (!listed.has(k) && now - c.at > 30 * 86400000) delete this.changed[k];
    localStorage.setItem("changed", JSON.stringify(this.changed));
  }

  // Keep offline pins until their terminal returns, and keep each pending until
  // core echoes the label so a failed write doesn't erase the old pin.
  migratePins(all) {
    const listed = new Map(all.filter((t) => t.state === "live").map((t) => [t.address, t]));
    const pending = this.pendingPins.filter((address) => {
      const terminal = listed.get(address);
      if (!terminal) return true;
      if (terminal.labels?.["herdr.pinned"]) return false;
      if (!this.migratingPins.has(address)) {
        this.migratingPins.add(address);
        run("terminal.pin", { terminal: address, pinned: true });
      }
      return true;
    });
    if (pending.length === this.pendingPins.length) return;
    this.pendingPins = pending;
    if (pending.length) localStorage.setItem("pins", JSON.stringify(pending));
    else localStorage.removeItem("pins");
  }

  since(a) {
    return this.changed[a.address]?.at ?? 0;
  }

  rank(a) {
    return RANK[a.status] ?? 0;
  }

  // The list's agents in its order, pinned first. A kept sub-agent (one with a
  // pane) is in agents() too: where a tree shows it (`nested`), it gets no row
  // of its own, unless it's pinned.
  ordered(all, collapsed) {
    const byPriority = sortOf().id === "priority";
    const every = agentsWithWaitingAsIdle(agents(), all);
    const nested = new Set(collapsed ? [] : every.flatMap((a) => this.tree(all, a.address).map((s) => s.t.address)));
    let list = every.filter((a) => a.pinned || !nested.has(a.address));
    const urgency = new Map(list.map((a) => [a.address, Math.max(this.rank(a), ...this.tree(all, a.address).map((s) => RANK[kidStatus(s.t, this.listed)] ?? 0))]));
    const rankOf = (a) => urgency.get(a.address);
    if (byPriority) list = [...list].sort((x, y) => rankOf(y) - rankOf(x) || this.since(y) - this.since(x) || y.seq - x.seq);
    // Pinned agents keep the order they were dragged into; newly pinned ones go last.
    const saved = shared(PIN_ORDER) ?? [];
    const place = (a) => (saved.includes(a.address) ? saved.indexOf(a.address) : saved.length);
    const pinned = list.filter((a) => a.pinned).map((a, i) => ({ a, i })).sort((x, y) => place(x.a) - place(y.a) || x.i - y.i).map((x) => x.a);
    const rest = list.filter((a) => !a.pinned);
    return { list: [...pinned, ...rest], pinned, rest, nested, rankOf };
  }

  // Each row's title: a tab someone named or the agent's own title, never an
  // automatic tab number unless two rows in a space would otherwise read the same.
  names(list) {
    const tabCounts = new Map();
    for (const a of list) tabCounts.set(a.tab, (tabCounts.get(a.tab) ?? 0) + 1);
    // Read the pane snapshot once: every host read copies the whole list into JS.
    const leads = leadPanes(panes());
    const tabs = new Map(spaces().flatMap(s => s.tabs).map(tab => [tab.id, tab]));
    const labels = new Map(list.map(a => {
      const tab = tabs.get(a.tab);
      const many = tabCounts.get(a.tab) > 1;
      return [a.address, tab ? title(many ? { ...tab, named: false } : tab, many ? a : leads.get(a.tab)) : (a.title || a.program?.name || "")];
    }));
    const label = (a) => labels.get(a.address);
    const detail = new Map(list.map(a => [a.address, /^\d+$/.test(label(a)) ? null : label(a)]));
    const seen = {};
    for (const a of list) { const k = `${a.space_name}\u0000${detail.get(a.address) ?? ""}`; seen[k] = (seen[k] ?? 0) + 1; }
    for (const a of list) {
      if (detail.get(a.address) == null && seen[`${a.space_name}\u0000`] > 1) detail.set(a.address, label(a));
    }
    return new Map(list.map((a) => [a.address, detail.get(a.address) ?? (/^\d+$/.test(label(a)) ? a.program?.name || label(a) : label(a))]));
  }

  // An agent's name with its inbox counts right after it.
  nameCell(t, address, name, color) {
    const inbox = this.inboxLine(t, address);
    return h_flex().flex_1().min_w(0).gap(8).items_center()
      .child(div().flex_shrink(1).min_w(0).truncate().text_color(color).child(name))
      .when(Boolean(inbox), (el) => el.child(inbox));
  }

  // The jumper's rows: top-level agents in the list's order, matching the typed filter.
  jumpRows(all) {
    this.listed = new Map(agentsWithWaitingAsIdle(agents(), all).map((a) => [a.address, a.status]));
    const { list, nested } = this.ordered(all, false);
    const top = list.filter((a) => !nested.has(a.address));
    const names = this.names(top);
    const filter = this.filter.toLowerCase();
    return top
      .map((a) => ({ a, name: names.get(a.address) }))
      .filter((r) => !filter || `${r.name} ${r.a.space_name ?? ""}`.toLowerCase().includes(filter));
  }

  jump(r) {
    if (!r) return;
    closeOverlay(props().overlay);
    run("pane.focus", { pane: r.a.pane });
  }

  // The agent jumper: type to filter, \u2191/\u2193 and Enter to go, Esc to leave.
  jumper(t) {
    const all = terminals();
    const rows = this.jumpRows(all);
    this.labelsOf = new Map(all.map((x) => [x.address, x.labels ?? {}]));
    // Opens on where you are.
    if (this.at === null) this.at = Math.max(0, rows.findIndex((r) => r.a.focused));
    this.at = Math.max(0, Math.min(this.at, rows.length - 1));
    // Rows sit at fixed offsets, and the list scrolls only as far as it takes
    // to keep the selected row in view.
    const total = rows.length * JUMP_ROW_HEIGHT;
    const height = Math.min(JUMP_LIST_HEIGHT, this.listHeight ?? JUMP_LIST_HEIGHT);
    const scrolls = total > height;
    const maxScroll = Math.max(0, total - height);
    this.scroll = Math.max(0, Math.min(this.scroll, maxScroll));
    const selectedTop = this.at * JUMP_ROW_HEIGHT;
    if (selectedTop < this.scroll) this.scroll = selectedTop;
    if (selectedTop + JUMP_ROW_HEIGHT > this.scroll + height) this.scroll = selectedTop + JUMP_ROW_HEIGHT - height;
    const inView = (i) => !scrolls || ((i + 1) * JUMP_ROW_HEIGHT > this.scroll && i * JUMP_ROW_HEIGHT < this.scroll + height);

    const row = (r, i) => {
      const { a, name } = r;
      const selected = i === this.at;
      const idle = a.status === "idle" && !a.focused;
      const machine = a.address.includes("/") ? a.address.split("/")[0] : null;
      const color = palette(t)[indexOf(this.labelsOf.get(a.address)?.[LABEL])];
      return h_flex()
        .id(`jump-${a.address}`)
        .px(8)
        .h(JUMP_ROW_HEIGHT)
        .flex_none()
        .rounded(8)
        .items_center()
        .gap(9)
        .when(a.focused && !selected, (el) => el.bg(t.selected))
        .when(selected, (el) => el.bg(t.hover))
        .cursor_pointer()
        .hover((s) => s.bg(t.hover))
        .on_click(() => this.jump(r))
        .when(scrolls, (el) => el.absolute().left(0).right(0).top(i * JUMP_ROW_HEIGHT - this.scroll))
        .child(spaceTile(t, { id: a.space, name: a.space_name, color }))
        .child(this.nameCell(t, a.address, name, idle ? t.muted : t.foreground))
        .when(Boolean(machine), (el) => el.child(div().text_size(10).text_color(t.dim).child(machine)))
        .child(this.status(t, a));
    };

    return v_flex()
      .id("jumper")
      .track_focus(this.handle)
      .w_full()
      .min_h(0)
      .p(18)
      .gap(10)
      .bg(t.popover)
      .text_size(13)
      .on_key_down((e, cx) => {
        if (e.key === "escape") return closeOverlay(props().overlay);
        if (!rows.length) return;
        if (e.key === "down") this.at = (this.at + 1) % rows.length;
        else if (e.key === "up") this.at = (this.at + rows.length - 1) % rows.length;
        else return;
        cx.stop_propagation();
        cx.notify();
      })
      .child(h_flex().items_center()
        .child(div().flex_1().text_size(15).font_semibold().text_color(t.foreground).child("Go to an agent"))
        .child(div().id("jumper-esc").px(6).py(2).rounded(5).text_size(11).text_color(t.dim).cursor_pointer()
          .hover((s) => s.bg(t.hover).text_color(t.foreground))
          .on_click(() => closeOverlay(props().overlay))
          .child("Esc")))
      .child(h_flex().h(36).flex_none().px(10).gap(8).items_center().rounded(9).border_1().border_color(t.accent).bg(t.background)
        .child(svg("mods/workspace-chrome/search.svg").size(15).text_color(t.dim))
        .child(Input.new(this.input).flex_1().h_full().text_size(12.8).text_color(t.foreground)))
      .child(rows.length === 0
        ? div().px(8).py(6).mb(6).text_size(12).text_color(t.dim).child(this.filter ? "No agent matches." : "No agents yet.")
        : v_flex()
          .id("jump-list")
          .on_size_change((size, cx) => {
            this.listHeight = size.height / settings().zoom;
            cx.notify();
          })
          .h(Math.min(JUMP_LIST_HEIGHT, total))
          .min_h(0)
          .mb(6)
          .when(scrolls, (el) => el.relative().overflow_hidden().on_scroll_wheel((e, cx) => {
            this.scroll = Math.max(0, Math.min(this.scroll - e.delta.y, maxScroll));
            // The selection follows the wheel, so the next frame doesn't scroll back to it.
            this.at = Math.max(Math.ceil(this.scroll / JUMP_ROW_HEIGHT), Math.min(this.at, Math.floor((this.scroll + height) / JUMP_ROW_HEIGHT) - 1));
            cx.notify();
          }))
          .children(rows.map((r, i) => (inView(i) ? row(r, i) : null)).filter(Boolean)));
  }

  // The inbox counts, drawn right after an agent's name; null when nothing waits.
  inboxLine(t, address) {
    const labels = this.labelsOf.get(address) ?? {};
    const dark = settings().dark !== false;
    const parts = INBOX_COUNTS.filter(([key]) => labels[key]).map(([key, onDark, onLight]) => ({ text: labels[key], color: dark ? onDark : onLight }));
    if (!parts.length) return null;
    return h_flex().flex_shrink(0).max_w(150).gap(5).text_size(11.5).children(parts.flatMap((part, i) => [
      ...(i ? [div().flex_none().text_color(t.dim).child("\u00b7")] : []),
      div().min_w(0).truncate().text_color(part.color).child(part.text),
    ]));
  }

  // Gives every live agent without a palette color the one fewest agents have,
  // oldest agent first, and returns each agent's color by address.
  ensureColors(list) {
    const live = new Map(terminals().filter((x) => x.state === "live").map((x) => [x.address, indexOf(x.labels?.[LABEL])]));
    for (const [address, index] of live) if (index != null) this.pendingColors.delete(address);
    const indexes = new Map(list.map((a) => [a.address, live.get(a.address) ?? this.pendingColors.get(a.address) ?? null]));
    const used = [...indexes.values()].filter((i) => i != null);
    for (const a of [...list].sort((x, y) => x.seq - y.seq)) {
      if (indexes.get(a.address) != null) continue;
      const index = nextIndex(used);
      used.push(index);
      indexes.set(a.address, index);
      this.pendingColors.set(a.address, index);
      hc(["label", a.address, `${LABEL}=${index}`]);
    }
    const colors = palette(theme());
    return new Map([...indexes].map(([address, index]) => [address, colors[index]]));
  }

  // An agent's right-click menu: pin or unpin it, and pick its color.
  agentMenu(t, address) {
    const agent = agents().find((a) => a.address === address);
    const pinned = Boolean(agent?.pinned);
    const tab = agent?.tab;
    const mine = indexOf(terminals().find((x) => x.address === address)?.labels?.[LABEL]);
    const done = () => closeOverlay(props().overlay);
    return v_flex()
      .w_full()
      .p(5)
      .bg(t.popover)
      .text_size(12.8)
      .child(h_flex()
        .id("agent-pin")
        .h(31)
        .px(8)
        .gap(9)
        .rounded(7)
        .items_center()
        .text_color(t.foreground)
        .hover((el) => el.bg(t.hover))
        .on_click(() => {
          run("terminal.pin", { terminal: address, pinned: !pinned });
          done();
        })
        .child(svg(`mods/workspace-chrome/${pinned ? "pin-off" : "pin"}.svg`).size(14).flex_none().text_color(t.muted))
        .child(div().flex_1().child(pinned ? "Unpin" : "Pin to the top")))
      .when(Boolean(tab), (el) => el.child(h_flex()
        .id("agent-rename")
        .h(31)
        .px(8)
        .gap(9)
        .rounded(7)
        .items_center()
        .text_color(t.foreground)
        .hover((s) => s.bg(t.hover))
        .on_click(() => {
          done();
          run("tab.rename", { id: tab });
        })
        .child(svg("mods/workspace-chrome/square-pen.svg").size(14).flex_none().text_color(t.muted))
        .child(div().flex_1().child("Rename"))))
      .child(div().mx(3).my(4).h(1).bg(t.border))
      .child(div().px(8).pt(2).pb(6).text_size(11).text_color(t.dim).child("Color"))
      .child(h_flex().px(8).pb(5).flex_wrap().gap(8).children(palette(t).map((color, index) => div()
        .id(`color-${index}`)
        .size(24)
        .rounded(6)
        .bg(color)
        .cursor_pointer()
        .border(2)
        .border_color(index === mine ? t.foreground : color)
        .on_click(() => {
          hc(["label", address, `${LABEL}=${index}`]);
          done();
        }))));
  }

  sortMenu(t) {
    const on = sortOf().id;
    return v_flex()
      .w_full()
      .p(5)
      .bg(t.popover)
      .text_size(12.8)
      .child(div().px(8).pt(4).pb(6).text_size(11).text_color(t.dim).child("Sort agents"))
      .children(SORTS.map((s) => h_flex()
        .id(`sort-${s.id}`)
        .h(31)
        .px(8)
        .gap(9)
        .rounded(7)
        .items_center()
        .text_color(t.foreground)
        .hover((el) => el.bg(t.hover))
        .on_click(() => {
          setShared("agents.sort", s.id);
          closeOverlay(props().overlay);
        })
        .child(svg(`mods/workspace-chrome/${s.icon}.svg`).size(14).flex_none().text_color(t.muted))
        .child(div().flex_1().child(s.label))
        .when(s.id === on, (el) => el.child(svg("mods/workspace-chrome/check.svg").size(14).flex_none().text_color(t.foreground)))));
  }

  peek(addr) {
    run("terminal.peek", { terminal: addr });
  }

  // Live sub-agents of `addr`, depth first, each with the tree lines in front of it.
  tree(all, addr, lead = []) {
    const kids = all.filter((k) => k.parent === addr && k.state === "live");
    return kids.flatMap((k, i) => {
      const last = i === kids.length - 1;
      return [
        { t: k, lines: [...lead, last ? "└" : "├"] },
        ...this.tree(all, k.address, [...lead, last ? "" : "│"]),
      ];
    });
  }

  subRow(t, s, shown, compact) {
    const k = s.t;
    const st = kidStatus(k, this.listed);
    const on = k.address === shown;
    const pic = icon(k.program, 14, on ? t.foreground : t.muted, true);
    // A sub-agent's questions go to its coordinator, so a blocked sub-agent gets a plain dot, never "Needs you".
    const blockedDot = div().size(7).mx(3).flex_none().rounded(4).bg(t.blocked);
    return h_flex()
      .id(`sub-${k.address}`)
      .pl(39)
      .pr(8)
      .rounded(8)
      .h(26)
      .flex_none()
      .gap(0)
      .items_center()
      .text_size(13.5)
      .when(on, (el) => el.bg(t.raised))
      .hover((s) => s.bg(t.hover))
      .on_click(() => (this.listed.has(k.address) ? run("pane.focus", { terminal: k.address }) : this.peek(k.address)))
      .children(treeLines(t, s.lines))
      .when(Boolean(pic), el => el.child(div().mr(8).child(pic)))
      .child(this.nameCell(t, k.address, k.name || k.template || k.program?.name || k.address, on ? t.foreground : t.muted))
      .child(st === "blocked" ? blockedDot : this.status(t, { status: st }, compact));
  }

  // An idle or done agent whose sub-agents, at any depth, are working shows as working.
  shownStatus(all, a) {
    const isResting = a.status === "idle" || a.status === "done";
    const subWorking = isResting && this.tree(all, a.address).some((s) => kidStatus(s.t, this.listed) === "working");
    return subWorking ? "working" : a.status;
  }

  // compact: a ! for blocked where a header already says "Needs you".
  status(t, a, compact = false) {
    const color = t[a.status] ?? t.dim;
    // Working: a dot that fades in and out. A slow fade stays smooth at the app's ~30 fps animation rate; a spinner shows every frame step.
    if (a.status === "working") return div().size(7).mx(3).flex_none().rounded(4).bg(t.working).loop("opacity", { from: 0.25, to: 1, period: 1700 });
    if (a.status === "blocked" && compact) return div().size(14).flex_none().rounded(4).bg(t.blocked + "26").text_size(10.5).font_bold().text_color(t.blocked).flex().items_center().justify_center().child("!");
    if (a.status === "blocked") return div().flex_none().px(6).h(18).rounded(5).bg(t.blocked + "26").text_size(10.5).font_semibold().text_color(t.blocked).flex().items_center().child("Needs you");
    // Idle: nothing, in the same space, so rows stay aligned; done: a steady dot.
    if (a.status === "idle") return div().size(7).mx(3).flex_none();
    return div().size(7).mx(3).flex_none().rounded(4).bg(a.status === "done" ? color : t.dim + "00");
  }

  render(cx) {
    const t = theme();
    if (props()?.sortMenu) return this.sortMenu(t);
    if (props()?.agentMenu) return this.agentMenu(t, props().agentMenu);
    if (props()?.newMenu) return newMenu(t, this);
    if (isJumper()) return this.jumper(t);
    const collapsed = narrow();
    const sort = sortOf();
    const byPriority = sort.id === "priority";
    const all = terminals();
    const every = agentsWithWaitingAsIdle(agents(), all);
    this.listed = new Map(every.map((a) => [a.address, a.status]));
    this.migratePins(all);
    this.colorOf = this.ensureColors(every);
    this.labelsOf = new Map(all.map((x) => [x.address, x.labels ?? {}]));
    const shown = focused()?.agent;
    // The agent on screen keeps its row marked while a mod pane beside it, such as the inbox, holds the keyboard.
    const focusedAgent = every.find((a) => a.focused);
    if (focusedAgent) this.viewedAgent = focusedAgent.address;
    const inShownTab = every.filter((a) => a.tab === focused()?.tab);
    const viewed = (focusedAgent ?? inShownTab.find((a) => a.address === this.viewedAgent) ?? inShownTab[0])?.address;
    this.track(every, Date.now());
    this.syncTabBar(every);
    const { list, pinned, rest, rankOf } = this.ordered(all, collapsed);
    setAgentOrder(list.map((a) => a.address));

    const { ownedBy, rest: unownedTabs } = collapsed ? { ownedBy: new Map(), rest: [] } : splitTabs(otherTabs(), pinned);
    const withKids = (a, i) => {
      const subs = collapsed ? [] : this.tree(all, a.address);
      const r = row(a, i, subs);
      if (this.folded.has(a.address)) return [r];
      const tabs = ownedBy.get(a.address);
      const shownSubs = tabs ? continued(subs) : subs;
      const toggle = (_e, cx) => {
        this.tabsOpen.has(a.address) ? this.tabsOpen.delete(a.address) : this.tabsOpen.add(a.address);
        cx.notify();
      };
      return [r, ...shownSubs.map((s) => this.subRow(t, s, shown, byPriority)), ...(tabs ? tabsSection(t, tabs, this.tabsOpen.has(a.address), toggle) : [])];
    };

    // Folds or opens an agent's sub-agents. With Option, every agent's, to match the one clicked.
    const toggleFold = (a, e, cx) => {
      cx.stop_propagation();
      this.press = null;
      this.dragged = null;
      this.dragOver = null;
      const closing = !this.folded.has(a.address);
      const targets = e.modifiers.alt ? list.filter((x) => this.tree(all, x.address).length > 0).map((x) => x.address) : [a.address];
      for (const address of targets) closing ? this.folded.add(address) : this.folded.delete(address);
      cx.notify();
    };

    const names = this.names(list);
    const dragging = Boolean(this.dragged);
    const pinnedIndex = (address) => pinned.findIndex((a) => a.address === address);
    // Moves a pinned agent to where another one is, keeping the saved places of pinned agents not shown now.
    const movePinned = (from, to) => {
      const order = pinned.map((a) => a.address).filter((address) => address !== from);
      order.splice(pinnedIndex(to) > pinnedIndex(from) ? order.indexOf(to) + 1 : order.indexOf(to), 0, from);
      const offline = (shared(PIN_ORDER) ?? []).filter((address) => !order.includes(address));
      setShared(PIN_ORDER, [...order, ...offline]);
    };

    const row = (a, i, subs = []) => {
      const pic = icon(a.program, 16, t.foreground);
      // An agent on another machine (`vps/t7`) says which.
      const machine = a.address.includes("/") ? a.address.split("/")[0] : null;
      if (collapsed) {
        return h_flex()
          .id(`ag-${a.address}`)
          .h(26)
          .px(8)
          .flex_none()
          .items_center()
          .gap(4)
          .when(a.address === viewed, (el) => el.bg(t.selected))
          .hover((s) => s.bg(t.hover))
          .on_click(() => run("pane.focus", { pane: a.pane }))
          .child(div().flex_1().min_w(0).whitespace_nowrap().text_right().text_color(t.muted).child(String(i + 1)))
          .child(h_flex().w(14).flex_none().items_center().justify_center().child(this.status(t, { ...a, status: this.shownStatus(all, a) }, true)));
      }
      const idle = a.status === "idle" && a.address !== viewed;
      const isPinned = a.pinned;
      // Under a space header the space goes without saying; without one (priority,
      // pinned) the space's letter tile stands in for the program icon.
      const full = byPriority || isPinned;
      // With sub-agents, hovering the row turns the tile's letter into the fold caret.
      const caret = subs.length > 0 && this.hovered === a.address
        ? `mods/workspace-chrome/chevron-${this.folded.has(a.address) ? "right" : "down"}.svg`
        : null;
      const lead = full || !pic || caret ? spaceTile(t, { id: a.space, name: a.space_name, color: this.colorOf.get(a.address), icon: caret }) : pic;
      return h_flex()
        .id(`ag-${a.address}`)
        .px(8)
        .h(32)
        .flex_none()
        .text_size(14)
        .rounded(8)
        .items_center()
        .gap(9)
        .when(a.address === viewed, (el) => el.bg(t.selected))
        .cursor_pointer()
        .hover((s) => s.bg(t.hover))
        .on_click(() => {
          this.press = null;
          run("pane.focus", { pane: a.pane });
        })
        .on_hover((hovered, cx) => {
          if (hovered) this.hovered = a.address;
          else if (this.hovered === a.address) this.hovered = null;
          cx.notify();
        })
        .when(isPinned, (el) => el
          .when(dragging && this.dragOver !== a.address && this.dragged === a.address, (el) => el.opacity(0.5))
          .when(dragging && this.dragOver === a.address && this.dragged !== a.address, (el) => el.relative().child(
            div().absolute().left(0).right(0).h(2).bg(t.accent)
              .when(pinnedIndex(this.dragged) < pinnedIndex(a.address), (line) => line.bottom(0))
              .when(pinnedIndex(this.dragged) > pinnedIndex(a.address), (line) => line.top(0))))
          .on_mouse_down("left", (e) => {
            const onFoldTile = this.pressOnFoldTile;
            this.pressOnFoldTile = false;
            this.press = onFoldTile ? null : { address: a.address, y: e.position.y };
          })
          .on_mouse_move((e, cx) => {
            const movedFarEnough = this.press && Math.abs(e.position.y - this.press.y) >= DRAG_START_DISTANCE;
            if (!this.dragged && movedFarEnough) this.dragged = this.press.address;
            if (this.dragged && this.dragOver !== a.address) {
              this.dragOver = a.address;
              cx.notify();
            }
          })
          .on_mouse_up("left", (_e, cx) => {
            const from = this.dragged;
            this.press = null;
            this.dragged = null;
            this.dragOver = null;
            if (from && from !== a.address) movePinned(from, a.address);
            cx.notify();
          }))
        .on_mouse_down("right", (_e, cx) => {
          cx.stop_propagation();
          openOverlay({ mod: "coordinator-agents", kind: "popup", at: "pointer", width: 168, height: 235, props: { agentMenu: a.address } });
        })
        .child(div().id(`agent-fold-${a.address}`)
          .when(subs.length > 0, el => el
            .on_mouse_down("left", () => { this.pressOnFoldTile = true; })
            .on_click((e, cx) => toggleFold(a, e, cx)))
          .child(lead))
        .child(v_flex().flex_1().min_w(0).justify_center()
          .child(h_flex().min_w(0).child(this.nameCell(t, a.address, names.get(a.address), idle ? t.muted : t.foreground)))
          .when(Boolean(machine), el => el.child(div().mt(-3).text_size(9.5).text_color(t.dim).truncate().child(machine))))
        .child(this.status(t, { ...a, status: this.shownStatus(all, a) }, byPriority && !isPinned));
    };

    // Groups in list order: spaces (grouped) or priority sections. Agents of a
    // space are next to each other in agents(), so neighbours make a group.
    const groups = pinned.length ? [{ id: "pinned", label: "Pinned", status: null, members: pinned }] : [];
    for (const a of rest) {
      const r = rankOf(a) || RANK.idle;
      const id = byPriority ? `sec-${r}` : `sp-${a.space}`;
      if (groups.at(-1)?.id === id) groups.at(-1).members.push(a);
      else groups.push({ id, label: byPriority ? SECTION[r] : a.space_name || "", status: byPriority ? SECTION_STATUS[r] : null, members: [a] });
    }

    if (unownedTabs.length) groups.push({ id: OTHER_TABS_GROUP, label: "Other tabs", status: null, members: unownedTabs, showSpace: true });

    // A group's header; it folds the group. Folded, it says how many and the most urgent state.
    const header = (g) => {
      const open = !this.folded.has(g.id);
      const top = g.members.reduce((m, a) => ((RANK[a.status] ?? 0) > (RANK[m] ?? 0) ? a.status : m), null);
      return h_flex()
        .id(g.id)
        .px(8)
        .pt(8)
        .h(30)
        .flex_none()
        .gap(6)
        .items_center()
        .text_size(11)
        .font_medium()
        .cursor_pointer()
        .hover((s) => s.text_color(t.foreground))
        .on_click((_e, cx) => {
          open ? this.folded.add(g.id) : this.folded.delete(g.id);
          cx.notify();
        })
        .child(div().truncate().text_color(t[g.status] ?? t.dim).child(g.label))
        .when(byPriority || !open, (el) => el.child(div().text_color(t.dim).child(String(g.members.length))))
        .child(svg(`mods/workspace-chrome/chevron-${open ? "down" : "right"}.svg`).size(11).flex_none().text_color(t.dim))
        .child(div().flex_1())
        .when(!open && !byPriority && top && top !== "idle", (el) => el.child(this.status(t, { status: top }, true)));
    };
    const rows = collapsed
      ? list.flatMap(withKids)
      : groups.flatMap((g) => [header(g), ...(this.folded.has(g.id) ? [] : g.members.flatMap((m, i) => (m.other ? [tabRow(t, m, { showSpace: g.showSpace })] : withKids(m, i))))]);

    return v_flex()
      .size_full()
      .min_h(0)
      .on_mouse_up("left", (_e, cx) => {
        this.press = null;
        if (!this.dragged) return;
        this.dragged = null;
        this.dragOver = null;
        cx.notify();
      })
      .bg(t.chrome)
      .border_t(1)
      .border_color(t.border)
      .text_size(13)
      .when(!collapsed, (el) =>
        el.child(
          h_flex()
            .px(16)
            .h(41)
            .pt(14)
            .pb(5)
            .flex_none()
            .items_center()
            .justify_between()
            .child(div().text_size(11.5).font_medium().text_color(t.dim).child("Agents"))
            .child(h_flex().gap(8).items_center()
              .child(headerButton(t, this))
              .child(
              div()
                .id("ag-sort")
                .text_size(11)
                .text_color(t.dim)
                .hover((s) => s.text_color(t.foreground))
                .on_click(() => openOverlay({ mod: "coordinator-agents", kind: "popup", at: "pointer", width: 200, height: 140, props: { sortMenu: true } }))
                .tooltip(`Sort agents: ${sort.label}`)
                .child(svg(`mods/workspace-chrome/${sort.icon}.svg`).size(14)),
            )),
        ),
      )
      .when(list.length === 0 && !collapsed, (el) =>
        el.child(div().px(12).text_size(12).text_color(t.dim).child("No agent status yet. Agents show up here when they report their status. Older versions and agents inside tmux may not.")),
      )
      .child(v_flex().id("ag-list").flex_1().min_h(0).px(collapsed ? 0 : 8).overflow_y_scrollbar().mode("always").children(rows))
      .when(collapsed, (el) =>
        el.child(
          h_flex()
            .h(28)
            .items_center()
            .justify_center()
            .child(
              div()
                .id("collapse")
                .text_size(13)
                .text_color(t.dim)
                .hover((s) => s.text_color(t.foreground))
                .on_click(toggleSidebar)
                .child("»"),
            ),
        ),
      );
  }
}
