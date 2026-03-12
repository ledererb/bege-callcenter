FROM python:3.12-slim

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r thinkai-voice-agent/requirements.txt

WORKDIR /app/thinkai-voice-agent
CMD ["bash", "start.sh", "start"]
