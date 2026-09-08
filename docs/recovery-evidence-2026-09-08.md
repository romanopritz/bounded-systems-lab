# Recovery Verification, 2026-09-08

## Scope

A protected online backup was created from the running single-server SQLite K3s
cluster. No service stop, datastore replacement, certificate rotation, firewall
change, or live restore was performed.

## Backup Evidence

- K3s version: `v1.36.4+k3s1`
- Datastore: embedded SQLite
- Encrypted archive bytes: `757424`
- Encrypted archive SHA256:
  `49cf02004fa81dc789f096bb6a79b132d42443afeb5a72b58abc6f399549c09b`
- Archive ownership/mode: `root:root`, `0600`
- Encryption-key ownership/mode: `root:root`, `0600`
- Cleartext files verified: `80`
- Certificate files parsed: `21`
- Per-file SHA256 manifest: verified
- Restored server-token mode: `0600`
- Required datastore, token, TLS, credential, manifest, and config material:
  present
- Extracted SQLite `PRAGMA integrity_check`: `ok`

The disposable verification extraction was removed automatically. The encrypted
archive and key remain outside Git. An off-host export is intentionally pending
explicit authorization because it moves encrypted cluster-administrator material
to another machine.

## Post-Check Health

The node remained Ready and both GitOps applications remained `Synced` and
`Healthy`. The public workload stayed at two Ready replicas with zero restarts.

This verifies archive creation and non-disruptive extraction/integrity checks. It
does not replace a full replacement-VM rehearsal, which remains the appropriate
place to start K3s from restored state.
