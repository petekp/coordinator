# Brief

The brief is the file a delegate reads first, and the contract you verify
its work against. Put it at `<worktree>/.scratch/BRIEF.md` for repo work,
or at `<backlog_dir>/briefs/<agent>/BRIEF.md` when the domain has no repo.
`delegate_start.py` makes Git ignore `.scratch/`. Start the delegate with
one line: "Read `<path>` and do what it says."

Fill every heading. Quote the user where they decided something, so the
delegate neither asks again nor widens it.

```markdown
# Brief: <task in a few words>

You own <what>, on branch `<branch>` in this worktree. Your coordinator is
the session in terminal `<coordinator's address>`. The user reads your
terminal too.

Read first: <repo guides, the plan or spec, by path>.

## Goal

<What done looks like, observable, in one or two sentences.>

## Decided

- <Decision>. The user, <date>: "<their words>".
- <Assumption made because asking would not change this brief.>
- <Choice the coordinator decided, and why.>

## Boundary

Change only <files, apps or areas>. Write only inside this worktree.
<Shared files you may touch, and how.> Anything else is a question for the
coordinator, not a change.

## Stop points

Push to `<branch>` once <the checks that gate the push, each by command> pass.
<When tests run only in CI: name the local checks that run before the push,
and say the task is done only when CI passes.>
Stop and report before a force-push, merging, bumping a version, releasing,
sending a message to a person, and changing CI or infrastructure. Also stop
before a second round of measurement or evaluation, and after two failed
attempts at the same check.
<When the task runs models from a script: the call limit.>
<Task-specific stop points, including a choice the user still has to make.>

## Report

Whenever you stop, write `REPORT.md` beside this brief, with:

- status: done, waiting on the user's yes, or stopped on a question
- what changed: files and commits
- what you verified and how: the command, the log path, the exit status
- what waits on the user, and any answer they gave you directly
- made outside the worktree: everything you created that removing this
  worktree would not remove, each with how to remove it. That covers
  running processes with PID or port, tmux sessions, browser tabs, virtual
  machines, folders, simulators with their UDIDs and other worktrees. Write
  "nothing" when there is none. The coordinator removes these when your
  task lands.

Then add these three headings, each with one list entry per item, and no
entries when it has none:

- `## Questions for the user`: each question or task for the user, with its
  options and your recommendation.
- `## Issues filed`: each issue you filed, by link, and whether this task
  fixes it.
- `## Findings`: each problem you noticed and did not fix, each
  work-around, and each part you did not check.

The coordinator's watcher counts the entries under each heading, so give
each item its own entry. Put questions and tasks for the user here, not in
an inbox of your own. The coordinator asks the user in one place and relays
their answers.

Send your coordinator no message for progress or for a question. Write
`REPORT.md` and end your turn with the same in your reply, and the
coordinator's fleet watcher wakes it. Use
`hc send <coordinator's address> "<text>"` only when you are blocked on
something another session holds, such as a shared check or a virtual
machine.
```
