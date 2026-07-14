FROM python:3.11-slim

WORKDIR /app

COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY server/app.py /app/app.py
COPY server/presets /app/presets

# Run as non-root to limit blast radius if the app is compromised
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENV DB_PATH=/data/scene_data.db
ENV CAMERAS_DIR=/data/cameras

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
