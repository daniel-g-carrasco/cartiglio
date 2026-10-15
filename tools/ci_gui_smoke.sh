#!/usr/bin/env bash
# CI helper: drive the bundled GUI under Xvfb, once as a libadwaita app and
# once on the plain GTK path (the one the Windows build uses): the folder
# handed on the command line must show up analysed. Nothing is written by
# the GUI on its own (dates change only on "Change dates"), so the check is
# the screenshot left in gui-<name>.png plus the process staying alive.
#
#   bash tools/ci_gui_smoke.sh [path/to/cartiglio]
set -euo pipefail

APP="${1:-./dist/cartiglio/cartiglio}"
PY="${PYTHON:-.venv/bin/python}"
export GSK_RENDERER=cairo GDK_BACKEND=x11

run_case() {  # name [VAR=value...]
    local name="$1"; shift
    local dir="gui-$name"
    rm -rf "$dir"; mkdir -p "$dir"
    for sample in "Ground floor" "Single-line diagrams"; do
        "$PY" tests/test_cartiglio.py --make-sample "$dir/$sample.pdf" >/dev/null
    done
    printf 'not a PDF\n' > "$dir/Report.pdf"   # shows up as an error row
    env "$@" xvfb-run -a -s "-screen 0 760x620x24" dbus-run-session -- bash -c '
        "$1" --gui "$2" &
        pid=$!
        sleep 12
        alive=0; kill -0 "$pid" 2>/dev/null && alive=1
        import -window root "$3" || true
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        test "$alive" = 1
    ' _ "$APP" "$dir" "gui-$name.png"
    echo "GUI smoke test passed: $name"
}

run_case adwaita
run_case gtk CARTIGLIO_NO_ADW=1
