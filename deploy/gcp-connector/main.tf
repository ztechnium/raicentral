# RAICentral GCP governance connector — least-privilege bootstrap (customer copy).
# Replace PROJECT_ID_PLACEHOLDER before apply.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

variable "project_id" {
  type        = string
  description = "GCP project to connect"
  default     = "PROJECT_ID_PLACEHOLDER"
}

resource "google_service_account" "raicentral_discover" {
  project      = var.project_id
  account_id   = "raicentral-discover"
  display_name = "RAICentral discovery"
}

resource "google_project_iam_member" "discover_asset_viewer" {
  project = var.project_id
  role    = "roles/cloudasset.viewer"
  member  = "serviceAccount:${google_service_account.raicentral_discover.email}"
}

resource "google_project_iam_member" "discover_model_armor_viewer" {
  project = var.project_id
  role    = "roles/modelarmor.viewer"
  member  = "serviceAccount:${google_service_account.raicentral_discover.email}"
}
