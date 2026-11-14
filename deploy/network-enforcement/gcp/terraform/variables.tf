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