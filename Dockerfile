# syntax=docker/dockerfile:1

# The container boundary is doing real work here, not just packaging: this
# process parses hostile PDFs, videos and Office files for a living, and the
# sanitisers it shells out to are large C and Perl parsers.

# ---- build the virtualenv separately, so no compilers ship in the image ----
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt


# ---- runtime ----
FROM python:3.13-slim

# ffmpeg strips metadata from audio and video. If it is absent the app drops
# those types from the allowlist and refuses them, rather than serving them
# unsanitised - so omitting it degrades safely, but loses the feature.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv/supload
COPY app ./app
COPY deploy/docker/healthcheck.py ./

# A fixed high UID, so the data volume has predictable ownership on the host.
RUN useradd --system --no-create-home --uid 10001 supload \
 && mkdir -p /srv/supload/data/blobs /srv/supload/data/tmp \
 && chown -R supload:supload /srv/supload/data

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SUPLOAD_DATA=/srv/supload/data

USER supload
EXPOSE 8000
VOLUME ["/srv/supload/data"]

# The app refuses requests whose Host it does not serve, so the health check
# has to present a real hostname - localhost gets a 404, which would look like
# a dead container.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "healthcheck.py"]

# One worker on purpose. The sanitiser semaphore is per-process, so two
# workers would allow two concurrent strips and defeat the memory cap; the
# app is async, so request concurrency is unaffected.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--no-access-log"]
