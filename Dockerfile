FROM python:3.12-slim

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r thinkai-voice-agent/requirements.txt

CMD ["bash", "thinkai-voice-agent/start.sh", "start"]
