# One image, three roles (API, worker, provider simulator), chosen by the command.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

# Dependencies first, code second. Docker caches each layer, so editing code doesn't
# re-download every package: this layer is rebuilt only when pyproject.toml changes.
COPY pyproject.toml .
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY simulator ./simulator
COPY app ./app
COPY scripts ./scripts

# Don't run as root: if the app is ever compromised, the attacker isn't root in the container.
RUN useradd --create-home --uid 10001 revive
USER revive

EXPOSE 8000
# Follows $PORT, because hosts like Render choose the port at runtime.
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health/live' % os.environ.get('PORT', '8000'))" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
