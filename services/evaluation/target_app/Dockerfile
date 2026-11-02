# Build from the repository root:
#   docker build -f services/evaluation/target_app/Dockerfile -t straph-target-app .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so this layer is cached until requirements.txt changes.
COPY services/evaluation/target_app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Only the files the app needs, not the whole repository.
COPY services/__init__.py services/__init__.py
COPY services/evaluation/__init__.py services/evaluation/__init__.py
COPY services/evaluation/target_app/ services/evaluation/target_app/

RUN useradd --system --no-create-home appuser
USER appuser

EXPOSE 8001

CMD ["uvicorn", "services.evaluation.target_app.app:app", "--host", "0.0.0.0", "--port", "8001"]
