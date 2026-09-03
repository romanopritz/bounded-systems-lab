# K3s Host Configuration

These files are a reviewed baseline for a single-node K3s server. Validate them
against the target host and K3s release before installation.

Apply `90-kubelet.conf` before starting K3s when using
`protect-kernel-defaults: true`. Install `psa.yaml` and `audit.yaml` at the paths
referenced by `config.yaml`.

DNS resolver configuration is deliberately excluded because it is host-specific.
If the host exposes more than three upstream nameservers, create a root-owned
resolver file containing at most three suitable servers and set K3s `resolv-conf`
to that path.

SSH ports, edge-firewall rules, local user certificates, registry credentials,
and provider-specific networking also belong in private environment configuration.
