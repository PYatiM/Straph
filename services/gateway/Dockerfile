# Build from the repository root:
#   docker build -f services/gateway/Dockerfile -t straph-gateway .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies layer
COPY services/gateway/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Application source code
COPY contracts/ contracts/
COPY services/__init__.py services/__init__.py
COPY services/gateway/ services/gateway/

RUN useradd --system --no-create-home appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "services.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
