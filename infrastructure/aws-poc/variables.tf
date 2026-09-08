variable "aws_region" {
  description = "AWS region for the disposable proof of concept."
  type        = string
  default     = "eu-central-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must look like a standard AWS region name."
  }
}

variable "enable_deployment" {
  description = "Explicit gate for creating any AWS resources."
  type        = bool
  default     = false
}

variable "desired_count" {
  description = "Fargate task count; zero preserves a control-plane-only deployment."
  type        = number
  default     = 0

  validation {
    condition     = var.desired_count >= 0 && var.desired_count <= 2 && floor(var.desired_count) == var.desired_count
    error_message = "desired_count must be an integer from zero through two."
  }
}

variable "image" {
  description = "Public OCI image pinned by sha256 digest."
  type        = string
  default     = "ghcr.io/romanopritz/bounded-systems-lab@sha256:ebe9f897359cbe2aa5485b3ca2c49890a20ddb2cb8f344cdbaf6d8dde8621e24"

  validation {
    condition     = can(regex("^[^[:space:]@]+@sha256:[0-9a-f]{64}$", var.image))
    error_message = "image must be an OCI reference pinned by a sha256 digest."
  }
}

variable "allowed_ingress_cidrs" {
  description = "Trusted IPv4 networks allowed to call port 8000; empty denies all ingress."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.allowed_ingress_cidrs : can(cidrhost(cidr, 0))
    ])
    error_message = "Every allowed ingress value must be a valid CIDR."
  }
}

variable "log_retention_days" {
  description = "Short CloudWatch Logs retention for the disposable proof of concept."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 3, 5, 7, 14], var.log_retention_days)
    error_message = "log_retention_days must be one of 1, 3, 5, 7, or 14."
  }
}
