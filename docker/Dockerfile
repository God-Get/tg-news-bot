FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# system deps for lxml/trafilatura
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    libxml2-dev libxslt1-dev zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -U pip \
  && pip install --no-cache-dir -r requirements.txt

COPY . /app

CMD ["python", "-m", "app.bot.main"]
