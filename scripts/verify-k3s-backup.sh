#!/usr/bin/env bash

set -euo pipefail
umask 077

readonly ARCHIVE="${1:?usage: verify-k3s-backup.sh ARCHIVE KEY_FILE}"
readonly KEY_FILE="${2:?usage: verify-k3s-backup.sh ARCHIVE KEY_FILE}"
readonly ITERATIONS="${OPENSSL_PBKDF2_ITERATIONS:-200000}"

for command in find openssl python3 sha256sum tar; do
  command -v "$command" >/dev/null
done
[[ -f "$ARCHIVE" ]]
[[ -f "$KEY_FILE" ]]

work_dir="$(mktemp -d)"
plain_archive="${work_dir}/k3s-sqlite.tar.gz"

cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT

openssl enc -d -aes-256-cbc -pbkdf2 -iter "$ITERATIONS" \
  -in "$ARCHIVE" -out "$plain_archive" -pass "file:${KEY_FILE}"

python3 - "$plain_archive" "$work_dir" <<'PY'
import pathlib
import sys
import tarfile

archive_path, destination = sys.argv[1:]
with tarfile.open(archive_path, "r:gz") as archive:
    for member in archive.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("archive contains an unsafe path")
    archive.extractall(destination, filter="data")
PY

root="${work_dir}/recovery"
data_dir="${root}/var/lib/rancher/k3s"
config_dir="${root}/etc/rancher/k3s"
state_db="${data_dir}/server/db/state.db"
server_token="${data_dir}/server/token"

(
  cd "$root"
  sha256sum --check --strict SHA256SUMS >/dev/null
)
[[ -f "$state_db" ]]
[[ -f "$server_token" ]]
[[ "$(stat -c %a "$server_token")" == "600" ]]
[[ -d "${data_dir}/server/tls" ]]
[[ -d "${data_dir}/server/cred" ]]
[[ -d "${data_dir}/server/manifests" ]]
[[ -f "${config_dir}/config.yaml" ]]
[[ -f "${root}/BACKUP-METADATA" ]]

python3 - "$state_db" <<'PY'
import sqlite3
import sys

with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as database:
    result = database.execute("PRAGMA integrity_check").fetchone()
if result != ("ok",):
    raise SystemExit("restored SQLite database failed its integrity check")
PY

certificate_count=0
while IFS= read -r -d '' certificate; do
  openssl x509 -in "$certificate" -noout >/dev/null
  certificate_count="$((certificate_count + 1))"
done < <(find "${data_dir}/server/tls" -type f -name '*.crt' -print0)
if ((certificate_count == 0)); then
  printf 'backup contained no parseable certificate files\n' >&2
  exit 1
fi

file_count="$(find "$root" -type f | wc -l)"
archive_sha256="$(sha256sum "$ARCHIVE" | cut -d ' ' -f 1)"
python3 - "$archive_sha256" "$file_count" "$certificate_count" <<'PY'
import json
import sys

print(
    json.dumps(
        {
            "archive_sha256": sys.argv[1],
            "certificate_files_verified": int(sys.argv[3]),
            "files_verified": int(sys.argv[2]),
            "required_material_present": True,
            "sqlite_integrity": "ok",
        },
        indent=2,
        sort_keys=True,
    )
)
PY
