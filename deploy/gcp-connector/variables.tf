variable "project_id" {
  type        = string
  description = "GCP project to connect"
}

variable "enable_enforcement_sa" {
  type        = bool
  description = "Create raicentral-enforce service account"
  default     = true
}

variable "wif_pool_id" {
  type        = string
  description = "Workload Identity Pool id (optional)"
  default     = "raicentral-pool"
}

variable "wif_provider_id" {
  type        = string
  description = "WIF provider id"
  default     = "raicentral-provider"
}

variable "wif_issuer_uri" {
  type        = string
  description = "OIDC issuer URI for RAICentral host (required for WIF)"
  default     = ""
}

variable "wif_allowed_audiences" {
  type        = list(string)
  description = "Allowed token audiences for WIF"
  default     = []
}
