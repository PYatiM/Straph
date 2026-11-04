# Build from repository root:
#   docker build -f services/ranker/Dockerfile -t straph-ranker .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies layer
RUN pip install --no-cache-dir scikit-learn confluent-kafka jsonschema pydantic

# Application source code
COPY contracts/ contracts/
COPY services/ services/

RUN useradd --system --no-create-home appuser
USER appuser

CMD ["python", "-m", "services.ranker.service"]
