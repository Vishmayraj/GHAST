FROM python:3.12-slim

WORKDIR /app

COPY ingestion/requirements.txt ./ingestion/requirements.txt
RUN pip install --no-cache-dir -r ingestion/requirements.txt

COPY ingestion/ ./ingestion/
COPY backend/models/migrations/ ./backend/models/migrations/
COPY scripts/migrate.py ./scripts/migrate.py

WORKDIR /app/ingestion
CMD ["python", "main.py"]
