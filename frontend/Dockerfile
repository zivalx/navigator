# Stage 1: Build the frontend
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
# Optional local-only build secret `corp_ca` (corporate root CA for a TLS-inspecting proxy): used
# inside this RUN step only (node appends it to its roots), never copied into a layer.
RUN --mount=type=secret,id=corp_ca,required=false \
    if [ -s /run/secrets/corp_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/corp_ca; fi; \
    npm ci
COPY . .
RUN npm run build

# Stage 2: Hold built files for the shared volume
FROM alpine:3.19
COPY --from=builder /app/dist /build
# Copy files into the volume mount point at runtime so they persist
CMD ["cp", "-r", "/build/.", "/usr/share/nginx/html/"]
