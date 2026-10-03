FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ispu_bot ./ispu_bot

VOLUME /app/data
CMD ["python", "-m", "ispu_bot"]
