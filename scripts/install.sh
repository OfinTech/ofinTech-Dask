#!/bin/sh
# Install OfinTech Desk on this machine and start it, for one person to try.
#
#   curl -fsSL https://ofintech.ai/install.sh | sh
#
# What it does: writes a compose file and a .env into ~/ofintech-desk, pulls the published engine image, starts
# it on 127.0.0.1:8700 and waits until it answers. Run it again to upgrade; it keeps the data and the key.
#
# What it is NOT: a server install. This binds to the loopback address and speaks plain HTTP, so nothing on the
# network can reach it. A server that other people sign in to needs a domain and a certificate --
# server/docker-compose.yml and docs/DEPLOYMENT.md.
#
# Settings, all optional:
#   OFINTECH_DIR=~/ofintech-desk     where the compose file, the .env and the data volume's name live
#   OFINTECH_PORT=8700               the port on 127.0.0.1
#   OFINTECH_IMAGE=ofintech0/ofintech-desk
#   OFINTECH_TAG=latest              a version is steadier for a customer: OFINTECH_TAG=1.5.0
#   OFINTECH_WAIT=90                 seconds to wait for it to answer; raise it on a slow or emulated machine
set -eu

DIR=${OFINTECH_DIR:-$HOME/ofintech-desk}
PORT=${OFINTECH_PORT:-8700}
IMAGE=${OFINTECH_IMAGE:-ofintech0/ofintech-desk}
TAG=${OFINTECH_TAG:-latest}
WAIT=${OFINTECH_WAIT:-90}

say() { printf '%s\n' "$*"; }
die() { printf '\n%s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- what this machine has

case ${OFINTECH_PORT:-8700} in
  ''|*[!0-9]*) die "OFINTECH_PORT must be a number; it is '$OFINTECH_PORT'." ;;
esac

command -v docker >/dev/null 2>&1 || die "Desk runs in Docker, and this machine has no docker command.
Install Docker Desktop (macOS, Windows) or Docker Engine (Linux) from https://docs.docker.com/get-started/get-docker/
and run this again."

docker version >/dev/null 2>&1 || die "Docker is installed but not running. Start Docker Desktop, or on Linux
'sudo systemctl start docker', and run this again. If docker needs sudo on this machine, see
https://docs.docker.com/engine/install/linux-postinstall/"

docker compose version >/dev/null 2>&1 || die "This Docker has no 'compose' command, which Desk's start file needs.
Docker Desktop includes it; on Linux install the docker-compose-plugin package. See
https://docs.docker.com/compose/install/"

# The published image is linux/amd64. On an arm64 machine (an Apple Silicon Mac, a Graviton server) Docker will
# run it emulated: it works, and it is slower. Said once, here, rather than left to be discovered.
case $(uname -m) in
  arm64|aarch64) say "Note: this machine is arm64 and the image is amd64, so Docker will emulate it. Desk works;
      expect it to be slower than it is on an amd64 machine." ;;
esac

# ---------------------------------------------------------------- the directory, and the key that must not change

NEW=yes
[ -f "$DIR/.env" ] && NEW=no
mkdir -p "$DIR"
cd "$DIR"

if [ "$NEW" = yes ]; then
    # 32 bytes, base64. The engine will not start without it and does not make one for you: it encrypts the
    # provider keys and the connection secrets a workspace stores, so a new one would lock them away for good.
    if command -v openssl >/dev/null 2>&1; then
        KEY=$(openssl rand -base64 32)
    elif [ -r /dev/urandom ]; then
        KEY=$(dd if=/dev/urandom bs=32 count=1 2>/dev/null | base64)
    else
        die "No openssl and no /dev/urandom on this machine, so there is nothing to make a key out of.
Install openssl and run this again."
    fi
    KEY=$(printf '%s' "$KEY" | tr -d '\n' | tr '+/' '-_' | tr -d '=')
    [ ${#KEY} -ge 43 ] || die "The key came out $(printf %s "$KEY" | wc -c) characters long instead of 43. Nothing was written."

    umask 077
    cat > .env <<ENV
# OfinTech Desk, written by scripts/install.sh. Keep this file: DESK_MASTER_KEY is what the stored provider
# keys and connection secrets are encrypted with, and a different one cannot read them. Back it up somewhere
# that is not this machine. Anyone who has it and the data volume has those secrets.
DESK_MASTER_KEY=$KEY
OFINTECH_IMAGE=$IMAGE
OFINTECH_TAG=$TAG
OFINTECH_PORT=$PORT
ENV
    say "Wrote $DIR/.env with a new key."
else
    say "Found $DIR/.env; keeping the key and the data that go with it."
    # Settings given on an upgrade win, and are written back so the compose file and the .env never disagree.
    for pair in "OFINTECH_IMAGE=$IMAGE" "OFINTECH_TAG=$TAG" "OFINTECH_PORT=$PORT"; do
        name=${pair%%=*}
        eval "given=\${$name:-}"
        [ -n "$given" ] || continue
        if grep -q "^$name=" .env; then
            tmp=$(mktemp "$DIR/.env.XXXXXX")
            grep -v "^$name=" .env > "$tmp"
            printf '%s\n' "$pair" >> "$tmp"
            cat "$tmp" > .env && rm -f "$tmp"
        else
            printf '%s\n' "$pair" >> .env
        fi
    done
    # Whatever the .env says is what compose will use, so read all three back: the address printed at the end
    # and the image named in the messages have to be the ones it is actually going to run.
    stored() { sed -n "s/^$1=//p" .env | tail -n 1; }
    PORT=$(stored OFINTECH_PORT); PORT=${PORT:-8700}
    IMAGE=$(stored OFINTECH_IMAGE); IMAGE=${IMAGE:-ofintech0/ofintech-desk}
    TAG=$(stored OFINTECH_TAG); TAG=${TAG:-latest}
fi

# ---------------------------------------------------------------- the compose file

# Rewritten every run, so an upgrade brings any change made to it here. Everything it can differ by is in .env.
umask 022
cat > compose.yml <<'YML'
# Written by scripts/install.sh. Settings live in .env beside it; this file is replaced on every run.
name: ofintech-desk

services:
  engine:
    image: ${OFINTECH_IMAGE:?}:${OFINTECH_TAG:?}
    restart: unless-stopped
    # The loopback address only, not every address: this install speaks plain HTTP, and nothing that speaks
    # plain HTTP should be reachable from the network with a company's ledger in it. A server install uses a
    # certificate instead -- server/docker-compose.yml.
    ports: ["127.0.0.1:${OFINTECH_PORT:?}:8700"]
    environment:
      DESK_MODE: server
      DESK_ALLOW_HTTP: "1"
      DESK_PUBLIC_URL: http://localhost:${OFINTECH_PORT:?}
      DESK_MASTER_KEY: ${DESK_MASTER_KEY:?}
    volumes: [desk_data:/var/lib/ofintech-desk]
    tmpfs: [/tmp]                       # OCR and upload spools; nothing in the container is written anywhere else
    cap_drop: [ALL]                     # the engine needs no Linux capability: it runs as uid 10001 on port 8700
    security_opt: [no-new-privileges:true]
    logging: { driver: json-file, options: { max-size: "20m", max-file: "5" } }
    cpus: 2.0
    mem_limit: 2g
    mem_reservation: 512m
    pids_limit: 512
    stop_grace_period: 30s

volumes:
  desk_data:
YML

# ---------------------------------------------------------------- start it

say "Pulling $IMAGE:$TAG ..."
docker compose pull --quiet 2>/dev/null || docker compose pull || die "Could not pull $IMAGE:$TAG.
If this machine reaches Docker Hub through a proxy, set it in Docker's settings. If Docker Hub answers that
too many pulls came from this address, 'docker login' with a free account and run this again."

say "Starting it ..."
docker compose up -d || die "Docker would not start it. If the message above says the port is in use, pick
another one: OFINTECH_PORT=8800 and run this again. 'docker compose -f $DIR/compose.yml logs' shows why."

# ---------------------------------------------------------------- wait, and say where it is

URL="http://localhost:$PORT"
say "Waiting for it to answer ..."
i=0
while [ "$i" -lt "$WAIT" ]; do
    if curl -fsS -o /dev/null "$URL/health" 2>/dev/null; then
        cat <<DONE

Desk is running at $URL

  The first person to sign up there becomes the administrator; everybody after that needs an invitation.
  With nothing in it yet, the overview offers "Try it with a demo company" -- one click and there is a whole
  invented company to look at, which you can remove again in one click.

  Stop it      docker compose -f $DIR/compose.yml down
  Start it     docker compose -f $DIR/compose.yml up -d
  Upgrade      run this installer again
  Logs         docker compose -f $DIR/compose.yml logs -f

  Back up $DIR/.env somewhere else. It holds the key the stored secrets are encrypted with.
DONE
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done

die "It started but has not answered $URL/health in $WAIT seconds. What it says is in:
  docker compose -f $DIR/compose.yml logs"
