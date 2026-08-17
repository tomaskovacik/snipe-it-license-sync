FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY microsoft_licenses.py atlassian_licenses.py slack_licenses.py bitbucket_licenses.py snipe_it_sync.py exit_codes.py ./
COPY docker-entrypoint.sh ./

RUN pip install --no-cache-dir . \
    && chmod +x docker-entrypoint.sh \
    && useradd --uid 1000 --create-home appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

USER appuser

ENTRYPOINT ["./docker-entrypoint.sh"]
