# Set up the coordinator workflow

You are a coding agent helping someone set up this workflow on their
computer. Work through the steps below in order. Before each install, tell
them in one line what you will install and why, and wait for their yes. Skip
anything already installed: check first, and say what you found.

Never use `sudo`, never edit shell startup files yourself, and never type a
password, token or sign-in for them. When a step needs them, give them the
exact command or click, and wait until they say it is done.

## What gets installed

| Tool | What it does | Needed |
|---|---|---|
| herdr-core (`hc`) | Runs each agent session in a terminal that stays alive | yes |
| Herdr app | Shows every session, with delegates nested under their coordinator | yes |
| Coordinator skills | `/coordinator` and `fleet-sweep`, from this repo | yes |
| Inbox plugin | One pane for every question, task and finding across sessions | recommended |
| Vignette | Screenshot tool that sends drawings to and from agent sessions; macOS 14 or later | optional |
| Coordinator sidebar | Herdr layout with pinned coordinators first, inbox counts and colored agent tiles | optional |

## 1. Check the basics

Run `uname -sm`, `git --version`, `python3 --version`, `curl --version`,
`tar --version`, `gh --version` and `claude --version`. Python must be 3.9
or newer. Report what is missing.

- Herdr runs on macOS (Apple silicon) and on Linux (x86_64 and arm64), which
  this setup covers. On Linux it needs a desktop session. On an Intel Mac,
  on Windows, or on a Linux machine reached only over SSH, stop and say so.
  The coordinator has been tested on macOS only.
- `gh` is needed for pull request status. When it is missing, suggest
  `brew install gh` on macOS, or the package from https://cli.github.com.
  When `gh auth status` fails, ask them to run `gh auth login` themselves.

## 2. Herdr and herdr-core

Skip when `hc --version` works and Herdr.app is in `~/Applications` or
`/Applications` (on Linux, when `herdr-app` runs). Otherwise download
Herdr's installer, read it, and tell them in two lines what it does before
running it:

```sh
curl -fsSL https://releases.herdr.dev/app/install.sh -o /tmp/herdr-install.sh
cat /tmp/herdr-install.sh
sh /tmp/herdr-install.sh
```

It installs the app, into `~/Applications` on macOS, and herdr-core with
its `hc` command into `~/.local/bin` when no herdr-core is installed. It
needs no `sudo`. If `~/.local/bin/hc --version` works but `hc --version`
does not, `~/.local/bin` is not on their PATH. Ask them to add this line to
their shell's startup file, such as `~/.zshrc`:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Until they open a new terminal, this session cannot run `hc` by name, so
use `~/.local/bin/hc` for the checks below.

Then ask them to open Herdr once. If herdr-core did not install, Herdr
offers to install it when it opens.

## 3. The coordinator skills

```sh
git clone https://github.com/petekp/coordinator.git ~/src/coordinator
~/src/coordinator/install.sh
```

Ask before using another folder than `~/src/coordinator`. The install links
the skills into `~/.claude/skills` and `~/.agents/skills`, so the clone must
stay where it is. Show them the script's output. It lists anything still
missing.

No configuration is needed to start. Ask whether they want either of these.
If so, create `~/.config/coordinator/` and write `config.json` there with
only the keys they chose. An unknown key makes every coordinator script
stop, so add nothing else:

- `pr_limits`: the most open pull requests they want from their account in
  a repo, such as `{"their-org/their-repo": 20}`.
- `delegate_claude_args`: `["--autocompact", "300000"]` makes delegates
  compact their context earlier, which keeps long tasks cheaper.

`config.example.json` in the clone lists every key.

Then ask whether they want the watcher check, and install it only on a yes.
A coordinator arms its fleet watcher again only when something wakes it,
so after a usage limit or a restart of Claude Code nothing may watch its
delegates. `scripts/watcher_check.py --send` runs outside every session,
finds each coordinator with work and no watcher, and sends it one line
asking it to arm the watcher again. It runs every ten minutes from a
scheduler. In both examples below, replace `/Users/you` with their home
folder and adjust `PATH` so it finds `hc`, `gh` and `python3`. Both write
errors to `~/.local/state/coordinator`, the default `state_dir`, so create
that folder first. When they set another `state_dir`, put that folder in
the error path instead.

On macOS, write `~/Library/LaunchAgents/coordinator.watcher-check.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>coordinator.watcher-check</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/you/src/coordinator/skills/coordinator/scripts/watcher_check.py</string>
    <string>--send</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/Users/you/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartInterval</key><integer>600</integer>
  <key>StandardErrorPath</key><string>/Users/you/.local/state/coordinator/watcher-check.err</string>
</dict>
</plist>
```

Load it with
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/coordinator.watcher-check.plist`.
To remove it, run
`launchctl bootout gui/$(id -u)/coordinator.watcher-check`, then delete the
file.

On Linux, add this line with `crontab -e`:

```
*/10 * * * * PATH=/home/you/.local/bin:/usr/local/bin:/usr/bin:/bin python3 /home/you/src/coordinator/skills/coordinator/scripts/watcher_check.py --send >> /home/you/.local/state/coordinator/watcher-check.err 2>&1
```

Each run logs what it found to `<state_dir>/watcher-check.jsonl`.

## 4. The Inbox plugin

Skip when `claude plugin list` shows `inbox`. Otherwise:

```sh
claude plugin marketplace add petekp/inbox
claude plugin install inbox@inbox
```

It takes effect in new Claude Code sessions, and in open ones after
`/reload-plugins`. Inside the Herdr app, the first `/inbox` offers to
install its Herdr panel: tell them to press Install.

## 5. Vignette (optional, macOS only)

Ask whether they want it. If they do:

```sh
url=$(curl -fsSL https://api.github.com/repos/petekp/vignette/releases/latest \
  | python3 -c 'import json,sys; print(next(a["browser_download_url"] for a in json.load(sys.stdin)["assets"] if a["name"].endswith(".dmg")))')
curl -fL "$url" -o /tmp/Vignette.dmg
hdiutil attach -nobrowse -quiet /tmp/Vignette.dmg -mountpoint /tmp/vignette-dmg
cp -R /tmp/vignette-dmg/Vignette.app /Applications/ || { mkdir -p ~/Applications && cp -R /tmp/vignette-dmg/Vignette.app ~/Applications/; }
hdiutil detach -quiet /tmp/vignette-dmg
```

If a command fails after the attach, still run the detach.

Then ask them to open Vignette. Its first launch asks for a screenshot
shortcut, folder access and Accessibility permission, and offers to add its
Claude Code plugin. Those choices are theirs.

## 6. The coordinator sidebar (optional)

Ask whether they want it. It is a Herdr layout from this repo. Its sidebar
lists pinned coordinators first, in the order they drag them, with each
coordinator's delegates under it. The tab bar hides while a coordinator's
tab shows. Each agent gets a colored tile, and the inbox's counts follow its
name. Their own layout stays, and they can switch back.

If they do:

1. Read `~/src/coordinator/herdr/RECIPE.md` and every file in
   `~/src/coordinator/herdr/`. Tell them in a few lines what the mods run
   and read. They run `git` in each space's folder, `hc machine ls` every
   15 seconds, and `hc label` to store each agent's color. They watch each
   local repo's git folder, and load icons from Herdr's own
   `workspace-chrome` mod.
2. Add it the way the section "Layouts and mods other people made" in
   `~/.config/herdr-app/AGENTS.md` describes. Copy
   `herdr/layouts/coordinator.json` and its settings picture
   `herdr/layouts/coordinator.png` into `~/.config/herdr-app/layouts/`.
   Copy each folder in `herdr/mods/` into `~/.config/herdr-app/mods/`.
   Never overwrite. When a name is taken, rename the new one in every place
   the recipe's "Watch out" section lists.
3. `herdr/keys.json` binds Cmd-K to the agent jumper while this layout
   shows. Before merging it into their `~/.config/herdr-app/keys.json`, tell
   them three things and ask. An edited keys file counts as theirs, so
   Herdr's new default shortcuts stop arriving. The binding overrides any
   Cmd-K they bound in `global`. A Cmd-K in their `terminal` section still
   wins.
4. Run `herdr-app check` on each mod you copied.
5. Switching changes their window, so ask first. Then run
   `herdr-app do layout.switch --name coordinator`.

The files are copies, so `git pull` does not update them. To update, copy
them again.

## 7. Check it all

Run `hc --version`, `claude plugin list`, and
`~/src/coordinator/install.sh`, which prints "already linked" for each skill
when it worked. If they added the sidebar, `herdr-app status` should show
no mod errors. Report each result in one line.

## 8. Hand over

Tell them how to start, in these words or close to them:

1. Open the Herdr app and open a terminal in it.
2. `cd` into a project, start `claude`, and run `/coordinator <name>`, such
   as `/coordinator web-app`.
3. The coordinator asks what the project covers, then takes requests.
   Anything bigger than a few files goes to a delegate in a new session,
   listed under the coordinator.
4. Ask it "where are we" at any time for a status table.

Delegates start in their Claude Code permission mode. With the default mode
they stop at permission prompts, and at a folder trust prompt in each new
worktree, until answered in their tab. Mention it once.

Finish by listing what you installed, what you skipped and why, and any step
still waiting on them.
