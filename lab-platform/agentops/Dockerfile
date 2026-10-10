# The agent service and its dashboard. Built from the kit root so it can reuse agentkit.
FROM python:3.12-slim
ARG INSTALL_BOTO3=false
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'
RUN if [ "$INSTALL_BOTO3" = "true" ]; then pip install --no-cache-dir boto3; fi
WORKDIR /app
COPY agentkit /app/agentkit
COPY lab-platform/agentops /app/agentops
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=3)"
CMD ["python", "-m", "agentops.app"]
