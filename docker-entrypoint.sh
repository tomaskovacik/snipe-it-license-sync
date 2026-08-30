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
run_scripts() {
    set -e
    python -m microsoft_licenses
    python -m atlassian_licenses
    python -m bitbucket_licenses
    python -m slack_licenses
    python -m snipe_it_sync
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
