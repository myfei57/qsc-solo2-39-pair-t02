FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY crushplant ./crushplant
COPY main.py ./

ENV CP_HOST=0.0.0.0 \
    CP_PORT=8080

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

CMD ["python", "main.py", "serve", "--host", "0.0.0.0", "--port", "8080", "--data-dir", "/data"]
