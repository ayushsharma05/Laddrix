#!/usr/bin/env bash
# One-time hardening and set-up of a fresh Ubuntu 24.04 server for MockTrade
# (AWS Lightsail, Mumbai). Safe to run again: every step checks before it changes anything.
#
#   git clone https://github.com/ayushsharma05/MockTrade.git /opt/mocktrade
#   sudo /opt/mocktrade/deploy/server-setup.sh
#
# What it does (docs/runbooks/server.md explains each step):
#   1. security updates installed now and automatically from then on
#   2. Docker + Compose; your user may run docker
#   3. firewall: only SSH, 80 and 443 in
#   4. SSH: keys only, no root login (refused if you have no key yet, so you can't lock yourself out)
#   5. clock kept in sync (logins use 6-digit time codes; Kite logins expire at 6 AM)
#   6. 2 GB swap (building the image on a 2 GB server needs it)
#   7. daily backup after market close (21:45 India time), 14 days kept on the server
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADMIN_USER="${SUDO_USER:-ubuntu}"
step() { printf '\n== %s\n' "$*"; }

step "1. updates"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq unattended-upgrades ufw fail2ban docker.io docker-compose-v2 sqlite3
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades

step "2. docker"
systemctl enable --now docker
if ! id -nG "$ADMIN_USER" | grep -qw docker; then
  usermod -aG docker "$ADMIN_USER"
  echo "added $ADMIN_USER to the docker group (log out and in again)"
fi

step "3. firewall"
# Docker publishes 80/443 for Caddy itself; the app's port is never published.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable
systemctl enable --now fail2ban

step "4. ssh"
KEYS="$(getent passwd "$ADMIN_USER" | cut -d: -f6)/.ssh/authorized_keys"
if [[ -s "$KEYS" ]]; then
  cat >/etc/ssh/sshd_config.d/90-mocktrade.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
  sshd -t
  systemctl reload ssh
  echo "ssh: keys only"
else
  echo "ssh NOT changed: $ADMIN_USER has no authorized key yet (add one, then run again)" >&2
fi

step "5. clock"
timedatectl set-ntp true
timedatectl show -p NTPSynchronized

step "6. swap"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
fi
swapon --show

step "7. daily backup"
install -d -m 700 /var/backups/mocktrade
cat >/etc/cron.d/mocktrade-backup <<EOF
# MockTrade: back up the databases after market close (16:15 UTC = 21:45 India), Mon-Fri,
# then copy the backup out of the container to /var/backups/mocktrade on this server.
SHELL=/bin/bash
15 16 * * 1-5 root { cd $REPO_DIR && docker compose -f deploy/compose.yaml exec -T app python -m mocktrade.tools.backup --to /data/backups --keep 14 && docker compose -f deploy/compose.yaml cp app:/data/backups/. /var/backups/mocktrade/ && find /var/backups/mocktrade -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +; } >>/var/log/mocktrade-backup.log 2>&1
EOF
chmod 644 /etc/cron.d/mocktrade-backup

step "done"
echo "Next: deploy/.env, then 'docker compose -f deploy/compose.yaml up -d --build' (docs/runbooks/server.md)."
