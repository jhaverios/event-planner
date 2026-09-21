FROM python:3.12-slim

# A CA bundle may be mounted when the host sits behind a TLS-inspecting proxy.
# Without it every outbound call to WATI and ZeptoMail fails verification, and
# the answer is to trust the proxy's CA, never to stop verifying.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN useradd --system --uid 10001 portal && chown -R portal /app
USER portal

EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
