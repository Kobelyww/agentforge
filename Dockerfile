# ---- Stage 1: build the frontend ----
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime ----
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# ForgeOps domain data ships inside the package; examples for MCP demo
COPY examples ./examples

COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Non-root runtime user
RUN useradd -m appuser && mkdir -p /app/data && chown -R appuser /app
USER appuser

ENV AGENTFORGE_HOST=0.0.0.0 \
    AGENTFORGE_PORT=8000 \
    AGENTFORGE_DATA_DIR=/app/data

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["agentforge", "serve", "--host", "0.0.0.0", "--port", "8000"]
