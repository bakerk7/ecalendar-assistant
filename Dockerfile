FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY gateway/requirements.txt gateway/requirements.txt
RUN pip install --no-cache-dir -r gateway/requirements.txt
COPY ecal.py ./
COPY gateway/ gateway/
RUN useradd -r -u 10001 ecal
USER ecal
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('GATEWAY_PORT','8080')+'/health',timeout=4)"
CMD ["python", "-m", "gateway"]
