#!/usr/bin/env bash
set -euo pipefail

section() {
  printf '\n== %s ==\n' "$1"
}

section "identity"
hostnamectl || true
id
pwd

section "os"
if [[ -r /etc/os-release ]]; then
  cat /etc/os-release
fi
uname -a

section "cpu"
nproc
lscpu | sed -n '1,28p'

section "memory"
free -h

section "storage"
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL
df -hT / /home 2>/dev/null || df -hT

section "network"
ip -br addr
ip route

section "listening ports"
ss -tulpn | sed -n '1,120p'

section "docker"
if command -v docker >/dev/null 2>&1; then
  docker --version
  docker info --format 'driver={{.Driver}} cgroup={{.CgroupVersion}} containers={{.Containers}} images={{.Images}}' 2>/dev/null || \
    printf 'docker daemon not accessible for this user\n'
else
  printf 'docker client not installed\n'
fi

section "tooling"
for cmd in git curl gcc make python3 uv codex rg jq docker; do
  printf '%-10s ' "$cmd"
  command -v "$cmd" || true
done

section "reboot flag"
if [[ -f /var/run/reboot-required ]]; then
  cat /var/run/reboot-required
else
  printf 'no reboot required\n'
fi
