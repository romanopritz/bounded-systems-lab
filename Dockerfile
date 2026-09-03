FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789

LABEL org.opencontainers.image.title="Bounded Systems Lab" \
    org.opencontainers.image.description="Bounded async work and production-minded platform examples" \
    org.opencontainers.image.licenses="Apache-2.0" \
    org.opencontainers.image.source="https://github.com/romanopritz/bounded-systems-lab"

ENV HOME=/nonexistent \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --no-log-init \
        --home-dir /nonexistent --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes \
    --requirement requirements.lock

COPY src/bounded_systems_lab ./bounded_systems_lab

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --start-interval=1s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).close()"]

CMD ["uvicorn", "bounded_systems_lab.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
