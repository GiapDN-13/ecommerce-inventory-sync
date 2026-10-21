FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY inventory_sync ./inventory_sync
COPY mock_systems ./mock_systems
RUN pip install --no-cache-dir .

EXPOSE 8080
CMD ["uvicorn", "inventory_sync.service:app", "--host", "0.0.0.0", "--port", "8080"]
