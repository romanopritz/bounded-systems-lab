#!/usr/bin/env bash

set -euo pipefail
umask 077

readonly DATA_DIR="${K3S_DATA_DIR:-/var/lib/rancher/k3s}"
readonly CONFIG_DIR="${K3S_CONFIG_DIR:-/etc/rancher/k3s}"
readonly BACKUP_DIR="${BACKUP_DIR:-/root/k3s-backups}"
readonly KEY_FILE="${KEY_FILE:-${BACKUP_DIR}/.backup-key}"
readonly ITERATIONS="${OPENSSL_PBKDF2_ITERATIONS:-200000}"

if [[ "${EUID}" -ne 0 ]]; then
  printf 'this backup must run as root\n' >&2
  exit 2
fi

for command in find k3s openssl python3 sha256sum tar; do
  command -v "$command" >/dev/null
done

readonly STATE_DB="${DATA_DIR}/server/db/state.db"
readonly SERVER_TOKEN="${DATA_DIR}/server/token"
[[ -f "$STATE_DB" ]]
[[ -f "$SERVER_TOKEN" ]]

install -d -m 0700 "$BACKUP_DIR"
if [[ ! -f "$KEY_FILE" ]]; then
  openssl rand -base64 48 >"$KEY_FILE"
  chmod 0600 "$KEY_FILE"
fi
[[ "$(stat -c %a "$KEY_FILE")" == "600" ]]

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="${BACKUP_DIR}/k3s-sqlite-${timestamp}.tar.gz.enc"
archive_checksum="${archive}.sha256"
stage="$(mktemp -d "${BACKUP_DIR}/.stage.XXXXXX")"
plain_archive="${stage}/k3s-sqlite.tar.gz"

cleanup() {
  rm -rf "$stage"
}
trap cleanup EXIT

root="${stage}/recovery"
install -d -m 0700 \
  "${root}${DATA_DIR}/server/db" \
  "${root}${DATA_DIR}/server" \
  "${root}${CONFIG_DIR}" \
  "${root}/etc/systemd/system"

python3 - "$STATE_DB" "${root}${DATA_DIR}/server/db/state.db" <<'PY'
import sqlite3
import sys

source_path, destination_path = sys.argv[1:]
with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
    with sqlite3.connect(destination_path) as destination:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
if result != ("ok",):
    raise SystemExit("SQLite backup failed its integrity check")
PY
chmod 0600 "${root}${DATA_DIR}/server/db/state.db"
install -m 0600 "$SERVER_TOKEN" "${root}${SERVER_TOKEN}"

for directory in tls cred manifests; do
  source="${DATA_DIR}/server/${directory}"
  if [[ -d "$source" ]]; then
    install -d -m 0700 "${root}${source}"
    cp -a "${source}/." "${root}${source}/"
  fi
done

if [[ -d "$CONFIG_DIR" ]]; then
  cp -a "${CONFIG_DIR}/." "${root}${CONFIG_DIR}/"
fi
for file in /etc/systemd/system/k3s.service /etc/systemd/system/k3s.service.env; do
  if [[ -f "$file" ]]; then
    cp -a "$file" "${root}${file}"
  fi
done

{
  printf 'created_at=%s\n' "$timestamp"
  printf 'datastore=sqlite\n'
  k3s --version | head -n 1
  printf 'k3s_binary_sha256='
  sha256sum "$(command -v k3s)" | cut -d ' ' -f 1
} >"${root}/BACKUP-METADATA"

(
  cd "$root"
  find . -type f ! -name SHA256SUMS -print0 |
    sort -z |
    xargs -0 sha256sum >SHA256SUMS
)

tar --acls --xattrs --numeric-owner --create --gzip \
  --file "$plain_archive" --directory "$stage" recovery
openssl enc -aes-256-cbc -salt -pbkdf2 -iter "$ITERATIONS" \
  -in "$plain_archive" -out "$archive" -pass "file:${KEY_FILE}"
chmod 0600 "$archive"
sha256sum "$archive" >"$archive_checksum"
chmod 0600 "$archive_checksum"

printf 'archive=%s\n' "$archive"
printf 'checksum=%s\n' "$archive_checksum"
printf 'key=%s\n' "$KEY_FILE"
