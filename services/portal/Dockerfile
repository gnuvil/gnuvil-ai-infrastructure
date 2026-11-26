FROM ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 AS uv
FROM python:3.12-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never
COPY services/portal/pyproject.toml services/portal/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY scripts/service_config.py ./
COPY services/portal/app.py ./
COPY services/portal/templates ./templates
COPY services/portal/static ./static
# Uploaded assets may arrive with mode 0600; the runtime user must be able to read them.
RUN chmod -R a+rX /app/static
USER 10001:10001
EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-proxy-headers", "--limit-concurrency", "64"]
