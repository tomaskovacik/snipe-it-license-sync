FROM python:3.14-slim

# uv is copied from its pinned official image (Dependabot's docker ecosystem bumps it).
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY microsoft_licenses.py atlassian_licenses.py slack_licenses.py bitbucket_licenses.py snipe_it_sync.py exit_codes.py quiet.py ./
COPY docker-entrypoint.sh ./

# --frozen installs exactly what uv.lock pins (uv verifies the locked hashes);
# --no-binary is not used, but --no-install-project skips building this repo.
# The venv lives outside /app and is put first on PATH.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev --no-install-project --no-cache \
    && chmod +x docker-entrypoint.sh \
    && useradd --uid 1000 --create-home appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

# The scripts are plain top-level modules under /app; the entrypoint runs them
# as `python -m ...` from /data, so /app has to be importable.
ENV PYTHONPATH=/app PATH=/opt/venv/bin:$PATH

USER appuser

ENTRYPOINT ["./docker-entrypoint.sh"]
