FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
# Con package-lock.json (recomendado: commitealo) la instalación es reproducible.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
