FROM python:3.11-slim

LABEL maintainer="Trading Vision"

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY main.py .
COPY static/ static/

EXPOSE 8501

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8501", "--workers", "1", "--log-level", "warning"]
