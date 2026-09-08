locals {
  create = var.enable_deployment ? 1 : 0
  name   = "bounded-systems-poc"
  tags = {
    Project   = "bounded-systems-lab"
    ManagedBy = "terraform"
    Lifecycle = "disposable"
  }
}

resource "aws_vpc" "this" {
  count = local.create

  cidr_block           = "10.42.0.0/24"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = local.name }
}

resource "aws_internet_gateway" "this" {
  count = local.create

  vpc_id = aws_vpc.this[0].id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count = local.create

  vpc_id                  = aws_vpc.this[0].id
  cidr_block              = "10.42.0.0/25"
  map_public_ip_on_launch = false

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table" "public" {
  count = local.create

  vpc_id = aws_vpc.this[0].id
  tags   = { Name = "${local.name}-public" }
}

resource "aws_route" "internet" {
  count = local.create

  route_table_id         = aws_route_table.public[0].id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this[0].id
}

resource "aws_route_table_association" "public" {
  count = local.create

  subnet_id      = aws_subnet.public[0].id
  route_table_id = aws_route_table.public[0].id
}

resource "aws_security_group" "service" {
  count = local.create

  name        = "${local.name}-service"
  description = "Default-deny ingress for the bounded service"
  vpc_id      = aws_vpc.this[0].id

  dynamic "ingress" {
    for_each = length(var.allowed_ingress_cidrs) == 0 ? [] : [1]
    content {
      description = "Explicitly trusted proof-of-concept clients"
      from_port   = 8000
      to_port     = 8000
      protocol    = "tcp"
      cidr_blocks = var.allowed_ingress_cidrs
    }
  }

  egress {
    description = "TLS for image pull and CloudWatch Logs"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-service" }
}

resource "aws_ecs_cluster" "this" {
  count = local.create

  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "service" {
  count = local.create

  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  count = local.create

  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "logs" {
  count = local.create

  statement {
    sid    = "WriteServiceLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.service[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "execution_logs" {
  count = local.create

  name   = "write-service-logs"
  role   = aws_iam_role.execution[0].id
  policy = data.aws_iam_policy_document.logs[0].json
}

resource "aws_ecs_task_definition" "service" {
  count = local.create

  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution[0].arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name                   = "api"
      image                  = var.image
      essential              = true
      readonlyRootFilesystem = true
      user                   = "10001:10001"
      stopTimeout            = 15
      portMappings = [
        {
          name          = "http"
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
          appProtocol   = "http"
        }
      ]
      environment = [
        { name = "LAB_MAX_CONCURRENCY", value = "2" },
        { name = "LAB_MAX_QUEUE_SIZE", value = "4" },
        { name = "LAB_WORK_TIMEOUT_SECONDS", value = "2" },
      ]
      linuxParameters = {
        initProcessEnabled = true
        capabilities = {
          drop = ["ALL"]
        }
        tmpfs = [
          {
            containerPath = "/tmp"
            size          = 16
            mountOptions  = ["rw", "noexec", "nosuid"]
          }
        ]
      }
      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c 'import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8000/readyz\", timeout=2)'",
        ]
        interval    = 10
        timeout     = 3
        retries     = 3
        startPeriod = 15
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.service[0].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "api"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "this" {
  count = local.create

  name                   = local.name
  cluster                = aws_ecs_cluster.this[0].id
  task_definition        = aws_ecs_task_definition.service[0].arn
  desired_count          = var.desired_count
  launch_type            = "FARGATE"
  enable_execute_command = false
  propagate_tags         = "SERVICE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100

  network_configuration {
    assign_public_ip = true
    security_groups  = [aws_security_group.service[0].id]
    subnets          = [aws_subnet.public[0].id]
  }

  depends_on = [
    aws_iam_role_policy.execution_logs,
    aws_route.internet,
    aws_route_table_association.public,
  ]
}
