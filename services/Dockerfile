# Simulated customs services (the system under test). One image, three entrypoints.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/services

WORKDIR /app

COPY services/requirements.txt ./services/requirements.txt
RUN pip install -r services/requirements.txt

COPY schemas ./schemas
COPY services/customs_services ./services/customs_services

RUN useradd --create-home --uid 10001 app
USER app

CMD ["python", "-m", "customs_services.intake"]
