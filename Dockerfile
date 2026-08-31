FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
COPY --from=prediction_module_src / /tmp/pundit-prediction-module

RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m pip install /tmp/pundit-prediction-module

RUN addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /data/predictions /data/datacollector \
    && chown -R app:app /data/predictions /data/datacollector

COPY --chown=app:app app ./app
COPY --chown=app:app config ./config

USER app

EXPOSE 8000

CMD ["uvicorn", "app.maintenance.api:app", "--host", "0.0.0.0", "--port", "8000"]
