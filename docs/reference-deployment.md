# Reference Deployment

## Scope

The project has been validated on a single-node Ubuntu 26.04 host using K3s.
Provider, address, hardware, resolver, and SSH details are intentionally excluded
from the repository.

## Security Boundaries

- Use root only for host inspection, patching, package installation, and system services.
- Give an unprivileged account ownership of the checkout and development tools.
- Keep that account out of `sudo` and the container-runtime group unless the role requires it.
- Lock password login when key-only SSH access is established.
- Keep an edge firewall active and use a host firewall as a second boundary.
- Do not expose the Kubernetes API, kubelet, or overlay-network ports publicly.
- Store Kubernetes client certificates and kubeconfigs outside the repository with mode `0600`.
- Manage user and group RoleBindings as private environment configuration.

## Toolchain Snapshot

The validated host used tools from its distribution repositories where practical:

- Git, curl, CA certificates, build tools, ripgrep, and jq
- Python development headers and virtual-environment support
- Docker Engine with Buildx and Compose
- upstream `kubectl` 1.36.4, verified against its published SHA-256 checksum
- `uv` 0.12.9 installed for the unprivileged account

Using distribution packages for the container runtime keeps the package trust and
patching path simple. A dedicated upstream `kubectl` client avoids coupling normal
client use to root-only K3s server configuration.

## K3s Baseline

- K3s v1.36.4+k3s1, with its release checksum verified
- K3s-managed containerd, separate from the host image-building runtime
- Traefik and ServiceLB disabled until an exercise requires external traffic
- Kubernetes secret encryption enabled with the `secretbox` provider
- Restricted Pod Security admission, with only `kube-system` exempt
- Bounded API audit-log age, count, and file size
- Kubelet system reservations, eviction thresholds, and per-pod PID limits
- Application pods running as a fixed non-root UID/GID with no added capabilities
- Read-only application root filesystems and no service-account token mounts
- Namespace quotas and default-deny network policies

The host swap device may remain enabled when the kubelet is configured to tolerate
it, while pods retain the default `NoSwap` behavior. Production control planes
should make this an explicit, tested decision.

## Environment-Specific Configuration

Keep these values outside the public repository:

- edge and host firewall source ranges
- SSH port and authorized keys
- DNS resolver addresses
- local user certificates and RoleBindings
- registry credentials
- provider-specific storage and networking

The reference workload is published to public GHCR, pinned by digest, and tracked
from the protected `main` branch by a namespace-scoped Argo CD installation. Argo
CD remains cluster-internal and receives write access only through separate Roles
in the workload and observability namespaces. Add ingress or persistent storage
only with a concrete workload, narrow firewall rules, and a recovery plan.

For stateless edge firewalls, account for the container network's source NAT port
selection when admitting return traffic. K3s Flannel masquerading uses fully
randomized source ports, which can fall outside the host's normal ephemeral range;
keep the ACK-only return rule broad enough for ports 1024-65535 without opening new
inbound TCP handshakes.
