output "cluster_arn" {
  description = "ECS cluster ARN, or null while deployment is disabled."
  value       = try(aws_ecs_cluster.this[0].arn, null)
}

output "service_name" {
  description = "ECS service name, or null while deployment is disabled."
  value       = try(aws_ecs_service.this[0].name, null)
}

output "task_definition_arn" {
  description = "Task definition ARN, or null while deployment is disabled."
  value       = try(aws_ecs_task_definition.service[0].arn, null)
}
