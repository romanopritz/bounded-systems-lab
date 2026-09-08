# Plan-Only AWS Proof Of Concept

This Terraform root maps the bounded service from fixed-capacity K3s to the
smallest AWS runtime that preserves its container contract: one ECS service on
Fargate. EKS was rejected for the proof of concept because its control plane,
node integration, and Kubernetes add-ons add cost and operational surface that
do not help validate this workload.

The tradeoff is deliberate. A single public subnet and Fargate public IP let the
task pull the public GHCR image without a NAT gateway. The security group has no
ingress by default, there is no load balancer or stable endpoint, and the task is
not highly available across zones. This is suitable for a short portability
test, not production. Production should use private subnets, VPC endpoints or a
deliberately costed NAT design, multi-zone placement, private ingress, managed
secrets, and service-level monitoring.

## Preserved Contract

- The public `v0.1.0` image is pinned by immutable digest.
- Fargate is fixed at 0.25 vCPU and 512 MiB, its smallest task size.
- The application keeps two workers, four waiting slots, and a two-second
  end-to-end deadline.
- The root filesystem is read-only, `/tmp` is a 16 MiB `tmpfs`, all Linux
  capabilities are dropped, and the process runs as UID/GID `10001`.
- The task has no task role and therefore receives no application AWS
  permissions. Its execution role can only write to its dedicated log group.
- ECS deployment rollback is enabled. Fault injection and ECS Exec are disabled.

The K3s Prometheus/Grafana stack is not reproduced. CloudWatch receives
short-retention logs, but production SLI parity would require a separately
designed metrics path and alerts.

## Apply Gate

No workflow runs `terraform plan` with credentials or runs `terraform apply`.
With the defaults, `enable_deployment = false` gives every AWS resource a count
of zero. Enabling the deployment still defaults `desired_count` to zero, so
Fargate compute requires two explicit choices.

CI performs only formatting, provider initialization without a backend,
validation, TFLint, and Trivy configuration scanning. Terraform 1.16.1, AWS
provider 6.62.0, TFLint 0.64.0, and the provider lock file are pinned.

## Review A Plan

Use short-lived credentials in a disposable AWS account. Do not put credentials,
account IDs, plan files, or state in Git.

```bash
cd infrastructure/aws-poc
terraform init
terraform plan -out=review.tfplan
terraform show review.tfplan
```

That default plan must contain no resources. To inspect the gated topology
without committing local values:

```bash
terraform plan \
  -var='enable_deployment=true' \
  -var='desired_count=0' \
  -out=review.tfplan
terraform show review.tfplan
```

Adding a trusted CIDR and a nonzero task count creates an internet-addressable
task, even though the security group limits inbound port 8000. This repository
does not automate that step.

## Cost And Destruction

The design intentionally omits EKS, a NAT gateway, and a load balancer. When
enabled, costs can still include Fargate task runtime, public IPv4 addresses,
CloudWatch Logs ingestion and storage, and ordinary data transfer. Check the
current [AWS Fargate pricing](https://aws.amazon.com/fargate/pricing/),
[VPC pricing](https://aws.amazon.com/vpc/pricing/), and
[CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/) for the selected
region, set a small account budget, and review the saved plan before any apply.

If an operator chooses to apply this example, first return `desired_count` to
zero and verify tasks stop. Then create and review a separate destroy plan:

```bash
terraform plan -destroy -out=destroy.tfplan
terraform show destroy.tfplan
terraform apply destroy.tfplan
```

Confirm in AWS that no ECS service, task, log group, ENI, public IPv4 address, or
IAM role remains. Local state is acceptable only for a disposable single-user
exercise and must be protected as sensitive data. Team use requires a separately
configured encrypted remote backend with locking and restricted access.
