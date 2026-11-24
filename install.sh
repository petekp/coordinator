#!/bin/sh
# Links the coordinator and fleet-sweep skills into ~/.claude/skills and
# ~/.agents/skills, then checks what they need.
#
#   ./install.sh            link both skills
#   ./install.sh --remove   remove the links this script made
#
# CLAUDE_SKILLS_DIR and AGENTS_SKILLS_DIR choose other folders.

set -eu

REPO=$(CDPATH='' cd "$(dirname "$0")" && pwd -P)
CLAUDE_DIR=${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}
AGENTS_DIR=${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}
SKILLS="coordinator fleet-sweep"

say() { printf '%s\n' "$*"; }

if [ "${1:-}" = "--remove" ]; then
    for dir in "$CLAUDE_DIR" "$AGENTS_DIR"; do
        for skill in $SKILLS; do
            link=$dir/$skill
            if [ -L "$link" ] && [ "$(readlink "$link")" = "$REPO/skills/$skill" ]; then
                rm "$link"
                say "removed $link"
            fi
        done
    done
    exit 0
fi

failed=0
for dir in "$CLAUDE_DIR" "$AGENTS_DIR"; do
    mkdir -p "$dir"
    for skill in $SKILLS; do
        link=$dir/$skill
        target=$REPO/skills/$skill
        if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then
            say "already linked: $link"
        elif [ -e "$link" ] || [ -L "$link" ]; then
            say "not linked: $link already exists. Move it aside, then run this again."
            failed=1
        else
            ln -s "$target" "$link"
            say "linked $link"
        fi
    done
done

say ""
missing=0
for tool in hc herdr-app claude git python3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        say "missing: $tool (see README.md, Requirements)"
        missing=1
    fi
done
if command -v python3 >/dev/null 2>&1 && ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    say "python3 is older than 3.9"
    missing=1
fi
command -v gh >/dev/null 2>&1 || say "not found: gh, which domains with a GitHub repo need"
[ "$missing" = 0 ] && say "Every required tool was found."

config=${COORDINATOR_CONFIG:-$HOME/.config/coordinator/config.json}
[ -f "$config" ] || say "No config at $config, so every setting takes its default. config.example.json shows the keys."

exit "$failed"
