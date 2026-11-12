FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Pinned dependencies first, in their own layer: code changes do not reinstall them.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-deps .

# Needed by the one-off migration job (`docker compose run --rm migrate`, or the `migrate`
# service that `docker compose up` runs before the API starts).
COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --system --uid 1001 lumina && chown -R lumina /srv
USER lumina

EXPOSE 8000
# Serves the API only. Migrations are a separate step; they are not run here.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
