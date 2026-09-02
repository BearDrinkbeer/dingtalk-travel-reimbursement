FROM node:24.20.0-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.30.4-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html

# The application Nginx does not need root or a privileged port. Compose adds
# read-only/rootfs and tmpfs constraints for its only writable runtime paths.
USER nginx
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
