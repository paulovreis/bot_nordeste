FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Diretório fixo para o Chromium — acessível pelo usuário app após o chown
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependências mínimas do SO (tzdata + certificados TLS)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Instala o Chromium e todas as suas dependências de SO via Playwright.
# Deve rodar como root (usa apt-get internamente).
# Ao final, transfere a propriedade do diretório para o usuário app.
RUN playwright install chromium --with-deps \
    && chown -R app:app /ms-playwright

COPY app /app/app

RUN mkdir -p /data && chown -R app:app /data
USER app

ENV DB_PATH=/data/bot.db

CMD ["python", "-m", "app.main"]
