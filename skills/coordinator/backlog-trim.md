# Trim a backlog

A coordinator reads everything outside its backlog's Done section at each
start and after each compaction. Everything outside Done stays under 40 KB,
about 10,000 tokens, and Done under 20 KB, about 5,000. The config's
`backlog_limit_kb` and `done_limit_kb` change these limits.
`scripts/backlog_check.py <domain>` prints each section's size. When the
fleet watcher prints a `rule ·` line about your backlog's size, trim it.

`<scripts>` below is this skill's `scripts/` folder.

## Steps

1. Save the text as it is now. When Git tracks the backlog, commit it if it
   has changes, and keep the hash of its last commit. Otherwise copy it to
   `<backlog_dir>/trims/<domain>-<date>.md`.
2. Take each finished item out of Open, leaving one line for it in Done.
   Cut each item that stays to its state now. Keep the user's words where
   they are the decision, each `Ask:` sentence, each dated step and each
   handle. Move what is still open in any other section into Open, and
   delete that section.
3. Keep a ledger file beside your work as you cut. List each item you
   dropped with the reason, and each quote of the user's with where it
   went. Words of theirs that govern later work go to Standing yes. Words
   about one open item stay in that item. Words about finished work are
   dropped and listed.
4. Run `<scripts>/backlog_check.py <domain> --cut-done`. It deletes the
   oldest lines of Done until Done is at most half its limit. When Git
   tracks the backlog, it deletes only lines the last commit holds. When
   Git does not, it first saves them to `<backlog_dir>/trims/` and prints
   the path.
5. Write `Trimmed <date>. Earlier text: <commit hash or saved copy>.` as
   the line under the title.
6. Run `<scripts>/backlog_check.py <domain> --since <hash> --ledger <file>`,
   or `--before <saved copy>` in place of `--since <hash>`. The ledger
   clears two kinds of `LOST` line. It clears the user's words when it
   quotes exactly those words with the reason they are dropped. It clears a
   sentence that held an open ask when it repeats the sentence with the
   reason, such as that the user answered. Put back every other `LOST` line
   as it was: a Standing yes entry, a live delegate's session and an open
   PR. Put back what each `CHECK` line names unless it is finished or
   answered.
7. Have one subagent that did not write the trim read the ledger against
   the earlier text, and put back what it shows was dropped in error.

The trim is done when the check prints no `LOST` line and no `OVER` line,
the reviewer's findings are handled, and the trim is saved. When `OVER`
still prints and only live items are left, cut each item to its state now,
and leave the detail to the PR, brief or report it links. If the line
remains, tell the user the size.

## A backlog too large to read

A delegate trims a backlog whose text outside Done is over 150 KB, about
37,000 tokens, because reading it would fill your context. The config's
`trim_by_delegate_over_kb` changes this size. Start one delegate for the
trim, with these steps as its brief and the backlog file as its
boundary. It reads the file section by section and checks what is live
against `fleet_status.py`, the open PRs and `hc ls`. Then it sends you one
list of "is this still open?" questions. It writes the trimmed text to a
new file and leaves the backlog itself to you.

Keep working meanwhile, and replace an item's text instead of adding a
note. Before its result lands, the delegate reads what you changed since
step 1, with `git diff <hash> -- <backlog path>` or `diff <saved copy>
<backlog path>`, and carries each change into its text. You approve the
result and copy it over the backlog. Then restart as SKILL.md's Restart
section says, so the old text leaves your context.
