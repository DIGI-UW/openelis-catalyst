FROM nginx:1.28-alpine

RUN apk add --no-cache openssl

COPY hapi-mtls-proxy.conf /etc/nginx/nginx.conf
