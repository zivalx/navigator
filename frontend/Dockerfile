# Stage 1: Build the frontend
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Hold built files for the shared volume
FROM alpine:3.19
COPY --from=builder /app/dist /build
# Copy files into the volume mount point at runtime so they persist
CMD ["cp", "-r", "/build/.", "/usr/share/nginx/html/"]
