#!/bin/sh
# Launch only this checkout's editable installation, including native Git Bash.
set -eu
if [ "$#" -eq 2 ]; then
    [ "$1" = cursor ] && [ "$2" = --observe-read-content ] || exit 2
elif [ "$#" -ne 1 ]; then
    exit 2
fi
case "$1" in codex|claude|cursor) ;; *) exit 2 ;; esac
if [ "$1" = claude ] && [ -n "${CURSOR_VERSION:-}" ]; then exit 0; fi
task_workspace_adapters=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 2
task_workspace_root=$(CDPATH= cd -- "$task_workspace_adapters/../../.." && pwd) || exit 2
task_workspace_entry="$task_workspace_adapters/$1.py"
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        task_workspace_python="$task_workspace_root/.venv/Scripts/python.exe"
        # Native Python needs a Windows argument path; do not rely on implicit conversion.
        task_workspace_entry=$(cygpath -m -- "$task_workspace_entry") || exit 2
        ;;
    *) task_workspace_python="$task_workspace_root/.venv/bin/python" ;;
esac
if [ ! -x "$task_workspace_python" ]; then
    printf '%s\n' 'TASK_WORKSPACE_SETUP_REQUIRED: Run Poetry setup in this checkout.' >&2
    exit 2
fi
shift
"$task_workspace_python" "$task_workspace_entry" "$@" || exit 2
