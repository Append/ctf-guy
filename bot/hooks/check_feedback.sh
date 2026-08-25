#!/bin/bash
# Live feedback hook for Claude Code (PostToolUse) and Codex (TaskStarted).
# Both tools pipe JSON with a "cwd" field to stdin.
# If _live_feedback.md has new content since last read, output it.
# Hook stdout is injected into the agent's context.

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
CWD="${CWD:-${WORKSPACE:-.}}"

FEEDBACK_FILE="$CWD/_live_feedback.md"
MARKER_FILE="$CWD/_feedback_read_marker"

if [ -f "$FEEDBACK_FILE" ]; then
  if [ ! -f "$MARKER_FILE" ] || [ "$FEEDBACK_FILE" -nt "$MARKER_FILE" ]; then
    cat "$FEEDBACK_FILE"
    touch "$MARKER_FILE"
  fi
fi
