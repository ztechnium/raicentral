variable "project_id" {
  type        = string
  description = "GCP project id"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Primary region"
}

variable "gateway_subnet_cidr" {
  type        = string
  default     = "10.10.1.0/24"
  description = "Subnet for RaiCentral gateway (GKE nodes or GCE)"
}

variable "workload_subnet_cidr" {
  type        = string
  default     = "10.10.2.0/24"
  description = "Subnet for AI application workloads"
}

variable "gateway_service_account_id" {
  type        = string
  default     = "rai-gateway"
  description = "Service account used by gateway pods/VMs"
}

variable "enable_enforce_firewall" {
  type        = bool
  default     = true
  description = "When false, workload internet deny rule is omitted (OBSERVE posture)"
}

variable "gateway_listen_port" {
  type        = number
  default     = 8000
  description = "TCP port the gateway container listens on (ILB + health check)"
}

variable "gateway_container_image" {
  type        = string
  description = "RaiCentral gateway container image (Artifact Registry or GCR)"
}

variable "gateway_database_url" {
  type        = string
  sensitive   = true
  description = "DATABASE_URL for the gateway container (prefer Secret Manager for production)"
  default     = ""
}

variable "gateway_container_extra_env" {
  type        = map(string)
  default     = {}
  description = "Additional environment variables for the gateway container"
}