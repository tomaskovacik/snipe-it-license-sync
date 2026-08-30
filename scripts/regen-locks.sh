#!/usr/bin/env bash
# Regenerate the committed lock artifacts from pyproject.toml.
#
#   uv.lock              - full resolution, source of truth (satisfies Sonar
#                          text:S8565: a predictable lock file must exist)
#   requirements.txt     - runtime deps only, pinned + hashed, for Docker
#   requirements-dev.txt - runtime + dev (pytest) deps, pinned + hashed, for CI
#
# Run this after changing dependencies in pyproject.toml, or after Dependabot
# bumps uv.lock, then commit the result.
set -euo pipefail
cd "$(dirname "$0")/.."

uv lock

uv export --frozen --no-dev --no-emit-project \
  --format requirements-txt -o requirements.txt

uv export --frozen --no-emit-project \
  --format requirements-txt -o requirements-dev.txt

echo "Regenerated uv.lock, requirements.txt, requirements-dev.txt"
