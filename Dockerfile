FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp

# Usuario sin privilegios con UID 1000, el mismo que el usuario del servidor,
# para que pueda escribir en las carpetas montadas state/, data/ y reports/.
RUN useradd --create-home --uid 1000 app

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot ./bot
COPY tests ./tests
RUN mkdir -p state data reports && chown -R app:app /app

USER app
CMD ["python", "-m", "bot.scheduler"]
