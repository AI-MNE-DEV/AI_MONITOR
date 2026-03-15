FROM python:3.12-slim AS base

LABEL maintainer="ai-monitor" \
      description="AI Monitor - Agente telemetrico Host + Docker"

# Evita .pyc e abilita output unbuffered per logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Installa solo dipendenze runtime (no dev tools)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
       fastapi==0.135.1 \
       uvicorn==0.41.0 \
       pydantic==2.12.5 \
       psutil==7.0.0 \
       docker==7.1.0 \
       websockets==16.0 \
       httpx==0.28.1

# Copia sorgenti
COPY contracts.py host_probe.py docker_probe.py storage_engine.py \
     alert_manager.py ws_streamer.py notifier.py retention.py main.py ./
COPY static/ ./static/

# Crea directory dati con permessi
RUN mkdir -p /app/data && chmod 777 /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
