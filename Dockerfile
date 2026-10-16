FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/London

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY courtsniper/ courtsniper/
COPY data/ data/
# Booking preferences; the target court/time is passed as job arguments.
COPY config.toml.example config.toml

RUN useradd --create-home app && chown -R app /app
USER app

ENTRYPOINT ["python", "-m", "courtsniper"]
CMD ["probe"]
