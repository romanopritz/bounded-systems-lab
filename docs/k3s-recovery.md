# K3s Recovery And Upgrade

The reference host is a single K3s server using the default embedded SQLite
datastore. The tested version at backup time is `v1.36.4+k3s1`. The lab has no
persistent volumes: application state is declarative in Git and the monitoring
data uses bounded `emptyDir` volumes. This procedure does not claim high
availability; recovery requires a replacement server and operator action.

## Recovery Material

The encrypted backup contains:

- an online, internally consistent copy of
  `/var/lib/rancher/k3s/server/db/state.db`;
- `/var/lib/rancher/k3s/server/token`, which K3s uses to encrypt confidential
  bootstrap data in the datastore;
- server `tls` and `cred` directories, including certificate authority private
  material;
- auto-deploy manifests and `/etc/rancher/k3s` configuration;
- the K3s systemd unit/environment when present;
- the exact K3s version and installed binary SHA256;
- a SHA256 manifest for every cleartext file inside the encrypted archive.

The server token, kubeconfig, credentials, datastore, and CA keys are all
sensitive. Possession is effectively cluster-administrator access. Never commit
the archive or encryption key, and do not place both in the same remote backup
system. The Git repository remains the source of truth for application, GitOps,
and observability manifests.

K3s logs, container images, containerd state, and ephemeral monitoring samples
are intentionally excluded. They are not required to rebuild the desired state
and would make the backup substantially larger.

## Create And Verify

Run on the server as root. The script uses SQLite's online backup API, so it does
not stop K3s. It creates the encryption key once with mode `0600`, encrypts each
archive with AES-256-CBC/PBKDF2, and writes a ciphertext checksum.

```bash
BACKUP_DIR=/root/k3s-backups scripts/backup-k3s-sqlite.sh
scripts/verify-k3s-backup.sh \
  /root/k3s-backups/k3s-sqlite-<timestamp>.tar.gz.enc \
  /root/k3s-backups/.backup-key
```

Verification decrypts into a temporary directory, applies safe archive path
filtering, checks every file hash, checks the token mode, parses all certificate
files, and runs SQLite `PRAGMA integrity_check`. The temporary cleartext is
removed on exit. Copy the encrypted archive and key to separately protected,
off-host locations, verify their modes and checksums there, and test decryption
again after transfer.

## Disposable Restore Test

The verification script is the non-disruptive restore test: it performs a full
safe extraction and validates the restored datastore and recovery material in a
new temporary directory. Starting a second K3s control plane on the production
host is intentionally excluded because it would require privileged networking,
mounts, and overlapping host resources.

For a full rehearsal, use a disposable VM with the same architecture and exact
K3s version, no route to production, and no reused hostname. Copy the encrypted
archive and key through separate channels, run the verifier, then follow the
replacement procedure below. Delete the VM and its disks after recording only
aggregate results.

## Replacement Procedure

1. Isolate the failed server. Do not start old and restored control planes at
   the same time.
2. Provision a clean supported Linux host and install the exact backed-up K3s
   binary without starting it.
3. Verify the binary SHA256 against `BACKUP-METADATA` and verify the archive with
   `verify-k3s-backup.sh`.
4. Stop K3s. Restore `/etc/rancher/k3s`, the server token, `tls`, `cred`,
   `manifests`, and the SQLite database to their original paths and ownership.
5. Ensure the token and sensitive configuration remain root-owned mode `0600`.
   Restore the systemd unit, run `systemctl daemon-reload`, and start K3s.
6. Verify the node is Ready, both Argo CD applications are `Synced` and `Healthy`,
   both application replicas are Ready, all monitoring targets are up, and the
   service passes readiness and a normal work request.
7. Reapply the host firewall baseline if the replacement host does not already
   have it. Do not disable the provider firewall for recovery.

If the server address changes, update only the environment-specific kubeconfig,
firewall source rules, DNS, and any configured TLS SAN through a reviewed change.
Do not edit encrypted bootstrap records in SQLite.

## Upgrade

Use a manual, pinned upgrade for this single node. An automated upgrade controller
adds a highly privileged controller and jobs, which is not justified for one
server.

1. Read the target K3s and Kubernetes release notes. Respect Kubernetes version
   skew and do not skip minor versions.
2. Confirm the node, workloads, Argo CD, and monitoring are healthy. Create and
   verify a fresh encrypted backup while the old version is still running.
3. Download the exact target K3s binary and its architecture-specific SHA256 file
   from the K3s GitHub release. Verify the checksum before installation.
4. Record the existing binary checksum and retain a protected copy of the old
   binary next to the pre-upgrade backup. Schedule a maintenance window; this
   single-server API will be temporarily unavailable.
5. Atomically install the verified binary at `/usr/local/bin/k3s`, restart the
   `k3s` service, and watch its journal. Preserve the existing config file and
   systemd arguments.
6. Repeat the same health, GitOps, monitoring, and service checks used for
   recovery. Run the bounded game-day smoke scenario only after normal health is
   established.

Kubernetes does not support a simple control-plane downgrade. Rollback requires
stopping K3s, restoring the datastore and server token from the pre-upgrade
backup, restoring the matching old binary, and starting K3s again. If restore
validation fails, keep K3s stopped and investigate from copies; do not repeatedly
modify the only backup.

Primary references: [K3s backup and restore](https://docs.k3s.io/datastore/backup-restore),
[manual upgrades](https://docs.k3s.io/upgrades/manual), and
[upgrade rollback](https://docs.k3s.io/upgrades/roll-back).

## Legacy Retirement Gate

The superseded private namespace and checkout are not recovery inputs. Remove
them only after all of the following are true:

- the encrypted backup and off-host copy pass verification;
- the public repository and Git history pass a final organization-name and
  credential scan;
- current Argo CD applications remain `Synced` and `Healthy`;
- the user explicitly approves deletion of the named namespace and checkout.

After deletion, verify the public workloads are unchanged and record only the
generic retirement result; do not commit legacy names or private content.
