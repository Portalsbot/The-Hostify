# The Hostify controller image.
# Customer repositories are built on the separate remote Docker Engine.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=10000 \
    SESSION_DIR=/data \
    BASE_DIR=/tmp/hostify_builds

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

RUN groupadd --gid 10001 hostify \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/hostify hostify \
    && mkdir -p /data /tmp/hostify_builds \
    && chown -R 10001:10001 /data /tmp/hostify_builds /home/hostify

COPY --chown=10001:10001 . /app

USER 10001:10001

EXPOSE 10000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 CMD ["python", "/app/healthcheck.py"]

CMD ["python", "/app/main.py"]
