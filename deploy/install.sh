#!/usr/bin/env bash
#
# Puts supload live on supload.org. Run as root, from the project directory:
#
#     sudo deploy/install.sh
#
# This is the point of no return: it opens ports 80 and 443, requests real
# Let's Encrypt certificates for supload.org, www.supload.org and
# files.supload.org, and starts accepting anonymous uploads from the internet.
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/srv/supload
USER=supload

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

step() { printf '\n== %s\n' "$1"; }

step "service account"
id -u "$USER" &>/dev/null || useradd --system --home "$DEST" --shell /usr/sbin/nologin "$USER"

step "swap (a 967MB box with no swap OOMs under concurrent uploads)"
if ! swapon --show --noheadings | grep -q .; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "2G swapfile added"
else
    echo "swap already present, leaving it alone"
fi

step "application at $DEST"
mkdir -p "$DEST"
tar -C "$SRC" -cf - app requirements.txt deploy | tar -C "$DEST" -xf -
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"
mkdir -p "$DEST/data/blobs" "$DEST/data/tmp"
chown -R "$USER:$USER" "$DEST"
chmod 750 "$DEST/data"

step "ffmpeg (audio and video sanitizing)"
command -v ffmpeg >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg

step "caddy"
if ! command -v caddy >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy
fi
install -m 644 "$SRC/deploy/Caddyfile" /etc/caddy/Caddyfile
mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy

step "systemd units"
install -m 644 "$SRC/deploy/supload.service" /etc/systemd/system/
install -m 644 "$SRC/deploy/supload-sweeper.service" /etc/systemd/system/
install -m 644 "$SRC/deploy/supload-sweeper.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable supload.service supload-sweeper.timer
systemctl restart supload.service
systemctl start supload-sweeper.timer

step "firewall"
# Debian ships the nft library but not the service; the package provides both
# /usr/sbin/nft and nftables.service, which loads /etc/nftables.conf at boot.
if ! systemctl list-unit-files nftables.service --no-pager | grep -q nftables; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o Dpkg::Options::=--force-confold nftables < /dev/null
fi
install -m 755 "$SRC/deploy/nftables.conf" /etc/nftables.conf
nft -c -f /etc/nftables.conf
systemctl enable --now nftables
nft -f /etc/nftables.conf

step "caddy up (this requests certificates)"
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
chown -R caddy:caddy /var/log/caddy
systemctl enable --now caddy
systemctl reload caddy || systemctl restart caddy

step "health"
sleep 3
curl -fsS -H "Host: supload.org" http://127.0.0.1:8000/healthz && echo " <- app"
systemctl is-active supload caddy

cat <<'DONE'

Live. Check:

    curl -sI https://supload.org/ | head -3

Operate with:

    cd /srv/supload && sudo -u supload SUPLOAD_DATA=/srv/supload/data /srv/supload/.venv/bin/python -m app.admin stats
    cd /srv/supload && sudo -u supload SUPLOAD_DATA=/srv/supload/data /srv/supload/.venv/bin/python -m app.admin reported
    cd /srv/supload && sudo -u supload SUPLOAD_DATA=/srv/supload/data /srv/supload/.venv/bin/python -m app.admin purge <url>

DONE
