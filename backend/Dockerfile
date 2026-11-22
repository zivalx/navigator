FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
# Optional local-only build secret `corp_ca` (corporate root CA for a TLS-inspecting proxy): used
# inside the download RUN step only, never copied into a layer; absent => identical to a normal build.
RUN --mount=type=secret,id=corp_ca,required=false \
    if [ -s /run/secrets/corp_ca ]; then \
      trap 'rm -f /tmp/ca-bundle.pem' EXIT; \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/corp_ca > /tmp/ca-bundle.pem; \
      export PIP_CERT=/tmp/ca-bundle.pem; \
    fi; \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 7000

# Migrate, then run the application
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7000"]
