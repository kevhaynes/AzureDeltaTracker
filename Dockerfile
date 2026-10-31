FROM python:3.15-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
# pip is removed afterwards: it is not needed at runtime and its vendored
# copies of urllib3/msgpack/setuptools otherwise show up in CVE scans.
# apk upgrade pulls in Alpine security fixes published after the base image was built.
RUN apk upgrade --no-cache \
 && pip install -r requirements.txt \
 && pip uninstall -y pip \
 && addgroup -S app && adduser -S -G app app

COPY app ./app
COPY wsgi.py .

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD wget -qO- http://127.0.0.1:8000/healthz || exit 1

# One worker keeps the in-memory cache shared; threads give concurrency.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", \
     "--timeout", "300", "--access-logfile", "-", "wsgi:app"]
