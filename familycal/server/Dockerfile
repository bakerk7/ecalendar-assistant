FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FAMCAL_DATA=/data TZ=America/Chicago
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt tzdata
COPY famcal/ famcal/
RUN useradd -r -u 10002 famcal && mkdir -p /data && chown famcal /data
USER famcal
EXPOSE 8091
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=4)"
CMD ["python", "-m", "famcal"]
