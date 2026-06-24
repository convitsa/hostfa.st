#!/bin/bash

# Start Celery worker in background
celery -A app.tasks.celery_app.celery worker --loglevel=info -Q starter,pro,scale,default &

# Start Celery beat in background
celery -A app.tasks.celery_app.celery beat --loglevel=info &

# Start FastAPI application
exec uvicorn app.main:app --host 0.0.0.0 --port 80
