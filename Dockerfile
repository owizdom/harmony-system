FROM python:3.14-slim

WORKDIR /app

# Install deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory for SQLite
RUN mkdir -p /app/data

ENV DB_PATH=/app/data/citizens.db
ENV HOST=0.0.0.0
ENV PORT=5050

EXPOSE 5050

# Production: Gunicorn
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
