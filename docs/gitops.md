# GitOps With Argo CD

## Design

The reference environment uses Argo CD 3.5.2 from immutable upstream commit
`e258ee23c3e52266d407572f4bcdfe7d9ed36cb5`. The namespace-scoped installation
does not create Argo CD cluster roles. A separate Kubernetes `Role` grants the
application controller access only to the namespaced resource kinds used by the
workload in `bounded-lab`.

The `AppProject` admits one public Git repository, one destination namespace, and
an explicit set of namespaced resource kinds. The application tracks protected
`main`, self-heals drift, prunes resources removed from Git, rejects an empty
render, and stops retrying after three failed sync attempts.

The local cluster registration watches only `bounded-lab`, disables cluster-scoped
resources, and contains no credential material. For the internal Kubernetes API,
Argo CD uses the application controller's mounted service-account token, whose
permissions are defined by the workload namespace `Role`.

Argo CD has no ingress or load balancer. Its namespace quota and default limits
bound aggregate resource use, while controller and repository worker counts keep
reconciliation concurrency small for a fixed-capacity host. The repository server
retries failed Git operations up to three times so an isolated upstream timeout
does not leave application status unknown.

The application controller uses Argo CD's normal RBAC-respect mode. It stops
watching API kinds that its namespace `Role` cannot list, without the additional
authorization-review calls made by strict mode. This keeps discovery bounded and
avoids granting read access to unrelated resources or Secrets.

## Bootstrap

Use a cluster administrator for the one-time CRD and control-plane installation.
The workload namespace remains an operator-owned boundary and is created before
Argo starts managing namespaced resources:

```bash
kubectl apply -f platform/kubernetes/base/namespace.yaml
kubectl apply --server-side --force-conflicts \
  -k platform/gitops/control-plane
kubectl wait --for=condition=Available deployment --all \
  -n argocd --timeout=5m
kubectl rollout status statefulset/argocd-application-controller \
  -n argocd --timeout=5m
kubectl apply -k platform/gitops/application
```

Server-side apply is used for the large Argo CD CRDs, following the upstream
installation guidance. The upstream manifests are fetched over HTTPS from the
pinned commit during rendering.

## Verification

```bash
kubectl get pods -n argocd
kubectl get appproject,application -n argocd
kubectl get application bounded-systems-lab -n argocd \
  -o jsonpath='{.status.sync.status}{" "}{.status.health.status}{"\n"}'
kubectl auth can-i update deployments \
  --as=system:serviceaccount:argocd:argocd-application-controller \
  -n bounded-lab
kubectl auth can-i update deployments \
  --as=system:serviceaccount:argocd:argocd-application-controller \
  -n default
```

The first authorization check should print `yes`; the second should print `no`.

## Private UI Access

Keep the API server private. On the host, bind the port-forward to loopback only:

```bash
kubectl port-forward service/argocd-server 18080:443 \
  --address 127.0.0.1 -n argocd
```

From a workstation, create an SSH tunnel to that loopback listener and open
`https://127.0.0.1:18080`. Retrieve the initial password only in a private terminal;
do not display it in a shared Screen session or store it in the repository.

## Rollback And Removal

Revert a workload change through a pull request. Automated sync then reconciles
the reverted Git state. To stop automated changes during an incident:

```bash
kubectl patch application bounded-systems-lab -n argocd \
  --type merge -p '{"spec":{"syncPolicy":{"automated":{"enabled":false}}}}'
```

Removing the Argo CD `Application` does not cascade-delete the workload because
the application resource intentionally has no resources finalizer. Remove the
application configuration before the control plane, and inspect remaining
resources before any namespace deletion.
