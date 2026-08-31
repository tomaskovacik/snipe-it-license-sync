#!/bin/bash
set -e

# Run from a separate output directory (not /app, where the installed
# scripts live) so a mounted volume for output never shadows the code.
mkdir -p "${OUTPUT_DIR:-/data}"
cd "${OUTPUT_DIR:-/data}"

# NAGIOS=true implies QUIET=true — Nagios only wants the diff summary and
# final status line, not each fetch script's own progress output.
if [[ "${NAGIOS:-false}" == "true" ]]; then
    export QUIET=true
fi

# -m runs each module as __main__ (unlike import+call), so the
# `if __name__ == "__main__":` exit-code handling in each script actually
# fires here too, not just when run as `python microsoft_licenses.py`.
#
# Returns the worst status across every script (0 OK < 1 WARNING < 2
# CRITICAL < 3 UNKNOWN). A fetch script that returns >=2 (host unreachable,
# misconfigured) aborts the run — syncing against half-fetched source data
# would check people in who are actually still licensed. A soft WARNING
# (e.g. one unresolvable email) does not abort, but is carried through so
# it isn't lost behind snipe_it_sync's own "OK".
run_scripts() {
    local worst=0 rc
    for mod in microsoft_licenses atlassian_licenses bitbucket_licenses slack_licenses; do
        rc=0
        python -m "$mod" || rc=$?
        [[ "$rc" -ge 2 ]] && return "$rc"
        [[ "$rc" -gt "$worst" ]] && worst=$rc
    done
    rc=0
    python -m snipe_it_sync || rc=$?
    [[ "$rc" -gt "$worst" ]] && worst=$rc
    return "$worst"
}

if [[ "${NAGIOS:-false}" == "true" ]]; then
    # Nagios (and most transports in front of it) treat the plugin's stdout
    # as a single-line status unless multi-line output is pre-encoded with
    # literal "\n" sequences instead of real newlines — Nagios itself then
    # splits on those into short vs long output. Collapse the whole run's
    # stdout into that form here so a plain `docker run` already produces
    # Nagios-ready output, instead of requiring a wrapper script to redo
    # this escaping outside the container.
    set +e
    OUT=$(run_scripts)
    STATUS=$?
    set -e
    printf '%s\n' "${OUT//$'\n'/\\n}"
    exit "$STATUS"
fi

run_scripts
