FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_OUTPUT_DIR=/data/web_output \
    TRANSCRIBER_PROVIDER=sarvam \
    LLM_PROVIDER=heuristic

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt

COPY . .

RUN mkdir -p /data/web_output \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 7860

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
