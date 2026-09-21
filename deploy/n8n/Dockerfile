# n8n plus a QR encoder.
#
# The client-facing ticket image must not depend on a free public QR API: those
# have no SLA, and a registration confirmation that silently loses its QR is a
# client standing at a door who cannot get in. qrcode is small, self-hosted and
# works offline.
#
# Pretix already renders the same QR inside its ticket PDF using Python's
# qrcode, but exposes no endpoint that returns the code on its own.
FROM docker.n8n.io/n8nio/n8n:latest

USER root

# certs/ is normally empty. On a machine behind a TLS-inspecting proxy, drop a
# PEM bundle in there and npm will trust it instead of failing with
# SELF_SIGNED_CERT_IN_CHAIN. Copying a directory works whether or not it has
# files in it; copying an optional file does not.
COPY certs/ /usr/local/share/ca-certificates/extra/

RUN set -eux; \
    if ls /usr/local/share/ca-certificates/extra/*.crt >/dev/null 2>&1; then \
      cat /usr/local/share/ca-certificates/extra/*.crt > /usr/local/share/ca-certificates/extra-bundle.crt; \
      npm config set cafile /usr/local/share/ca-certificates/extra-bundle.crt; \
      echo "using extra CA bundle for npm"; \
    fi; \
    npm install -g qrcode@1.5.4; \
    npm config delete cafile || true; \
    chown -R node:node /usr/local/lib/node_modules/qrcode

USER node

# Lets the Code node require it. Paired with NODE_FUNCTION_ALLOW_EXTERNAL in compose.
ENV NODE_PATH=/usr/local/lib/node_modules
