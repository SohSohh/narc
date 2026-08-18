FROM python:3.11-slim

WORKDIR /app

# Install deps first so this layer is cached across code-only changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

# --proxy-headers so it plays nicely behind a reverse proxy if you put one in front
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
