# The deploy webhook: Python (standard library) plus the Docker CLI with the compose plugin and git.
FROM docker:27-cli
RUN apk add --no-cache python3 git
WORKDIR /app
COPY webhook.py deploy.sh /app/
RUN chmod +x /app/deploy.sh
EXPOSE 9000
HEALTHCHECK --interval=60s --timeout=5s CMD wget -q -O /dev/null http://127.0.0.1:9000/hooks/deploy/health || exit 1
CMD ["python3", "-u", "/app/webhook.py"]
