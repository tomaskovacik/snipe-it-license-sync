#!/bin/sh
set -e

# Run from a separate output directory (not /app, where the installed
# scripts live) so a mounted volume for output never shadows the code.
mkdir -p "${OUTPUT_DIR:-/data}"
cd "${OUTPUT_DIR:-/data}"

# -m runs each module as __main__ (unlike import+call), so the
# `if __name__ == "__main__":` exit-code handling in each script actually
# fires here too, not just when run as `python microsoft_licenses.py`.
python -m microsoft_licenses
python -m atlassian_licenses
python -m bitbucket_licenses
python -m slack_licenses
python -m snipe_it_sync
