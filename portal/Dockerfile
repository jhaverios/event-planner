FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# certs/ is normally empty. On a host behind a TLS-inspecting proxy, drop a PEM
# bundle in there and both the build and the running container trust it, rather
# than turning verification off. Copying a directory works whether or not it has
# files in it; copying an optional file does not.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN set -eux; \
    if ls /usr/local/share/ca-certificates/extra/*.crt >/dev/null 2>&1; then \
      cat /usr/local/share/ca-certificates/extra/*.crt \
        >> /etc/ssl/certs/ca-certificates.crt; \
      echo "trusting the extra CA bundle"; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir \
      --cert /etc/ssl/certs/ca-certificates.crt \
      -r requirements.txt

COPY . .

# httpx reads SSL_CERT_FILE, so outbound calls to WATI and ZeptoMail trust the
# same bundle the build used.
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN useradd --system --uid 10001 portal && chown -R portal /app
USER portal

EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
