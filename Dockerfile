FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
COPY microsoft_licenses.py atlassian_licenses.py slack_licenses.py bitbucket_licenses.py snipe_it_sync.py exit_codes.py quiet.py ./
COPY docker-entrypoint.sh ./

# --require-hashes + a fully pinned requirements.txt (generated from uv.lock)
# makes the resolved dependency set reproducible; --only-binary :all: refuses
# sdists so no package build/setup script runs during the image build.
RUN pip install --no-cache-dir --require-hashes --only-binary :all: -r requirements.txt \
    && chmod +x docker-entrypoint.sh \
    && useradd --uid 1000 --create-home appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

# The scripts are plain top-level modules under /app; the entrypoint runs them
# as `python -m ...` from /data, so /app has to be importable.
ENV PYTHONPATH=/app

USER appuser

ENTRYPOINT ["./docker-entrypoint.sh"]
