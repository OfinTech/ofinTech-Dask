# The public website: static files served by nginx as a non-root user on port 8080.
#   docker build -t ofintech-site .
#   docker run -d -p 8080:8080 --name ofintech-site ofintech-site
#
# Everything in this folder ships except the files that describe how to ship it; see .dockerignore. A page added
# here is served without touching this file, which is the point -- the previous arrangement listed the pages and
# a new one would have been left behind.
FROM nginxinc/nginx-unprivileged:1.31-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=nginx:nginx . /usr/share/nginx/html/

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
