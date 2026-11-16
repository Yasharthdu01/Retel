FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN addgroup --system clinic && adduser --system --ingroup clinic clinic
COPY pyproject.toml ./
COPY app app
RUN pip install .
COPY alembic alembic
COPY alembic.ini ./
COPY scripts scripts
RUN chown -R clinic:clinic /app
USER clinic
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/api/v1/health' % os.environ.get('PORT', '8000'))"
CMD ["sh", "-c", "alembic upgrade head && python -m scripts.seed && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
