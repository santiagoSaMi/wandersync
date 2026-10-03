FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /home/app/.prefect && chown -R app:app /home/app
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY ingestion ./ingestion
USER app
CMD ["python", "-m", "ingestion.serve"]
