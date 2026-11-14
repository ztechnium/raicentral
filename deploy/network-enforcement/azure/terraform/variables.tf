variable "resource_group_name" {
  type        = string
  description = "Resource group for reference deployment"
}

variable "location" {
  type        = string
  default     = "eastus"
  description = "Azure region"
}

variable "spoke_address_prefix" {
  type        = string
  default     = "10.20.1.0/24"
}

variable "gateway_address_prefix" {
  type        = string
  default     = "10.20.2.0/24"
}

variable "hub_address_prefix" {
  type        = string
  default     = "10.20.0.0/24"
}

variable "enable_enforce_nsg" {
  type        = bool
  default     = true
  description = "When false, NSG rules are documented in OBSERVE posture only"
}