# OfinTech Desk landing page: static files served by nginx, running as a non-root user on port 8080.
#   docker build -t ofintech-landing .
#   docker run -d -p 8080:8080 --name ofintech-landing ofintech-landing
FROM nginxinc/nginx-unprivileged:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=nginx:nginx site/ /usr/share/nginx/html/

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
