FROM python:3.11-slim

ARG MONGODB_URL
ARG MONGODB_DB_NAME
ARG REDIS_URL
ARG SECRET_KEY
ARG AZURE_STORAGE_CONNECTION_STRING
ARG AZURE_STORAGE_CONTAINER
ARG CF_API_TOKEN
ARG CF_ACCOUNT_ID
ARG STRIPE_SECRET_KEY
ARG STRIPE_WEBHOOK_SECRET
ARG RESEND_API_KEY
ARG FRONTEND_URL
ARG PAGESPEED_API_KEY

ENV MONGODB_URL=$MONGODB_URL
ENV MONGODB_DB_NAME=$MONGODB_DB_NAME
ENV REDIS_URL=$REDIS_URL
ENV SECRET_KEY=$SECRET_KEY
ENV AZURE_STORAGE_CONNECTION_STRING=$AZURE_STORAGE_CONNECTION_STRING
ENV AZURE_STORAGE_CONTAINER=$AZURE_STORAGE_CONTAINER
ENV CF_API_TOKEN=$CF_API_TOKEN
ENV CF_ACCOUNT_ID=$CF_ACCOUNT_ID
ENV STRIPE_SECRET_KEY=$STRIPE_SECRET_KEY
ENV STRIPE_WEBHOOK_SECRET=$STRIPE_WEBHOOK_SECRET
ENV RESEND_API_KEY=$RESEND_API_KEY
ENV FRONTEND_URL=$FRONTEND_URL
ENV PAGESPEED_API_KEY=$PAGESPEED_API_KEY
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium


RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    chromium \

    fonts-liberation \
    fonts-noto-color-emoji \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libxshmfence1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and Chromium browser binary dependencies
RUN playwright install chromium --with-deps

EXPOSE 80

COPY . /app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]


