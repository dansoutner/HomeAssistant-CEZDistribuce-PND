FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Knihovny pro Chromium headless shell (seznam z Playwrightu pro Debian 12) bez Xvfb, mesa
# a CJK fontů, které instaluje `playwright install --with-deps`. Font jen kvůli screenshotům.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 \
        libdrm2 libgbm1 libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 \
        libxcomposite1 libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && playwright install --only-shell chromium \
    && rm -rf /root/.cache

COPY pnd.py /pnd.py

CMD ["python3", "-u", "/pnd.py"]
