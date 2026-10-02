FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# review_stats.py is shared with the CLI so the API and `python review_stats.py` cannot disagree.
COPY scoring/review_stats.py ./scoring/review_stats.py
COPY backend/api/ ./backend/api/

ENV PYTHONPATH=/app/scoring
WORKDIR /app/backend/api
EXPOSE 8000
# GHAST_API_KEY and POSTGRES_DSN come from the compose environment.
CMD ["uvicorn", "main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
