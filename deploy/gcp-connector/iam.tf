resource "google_service_account" "raicentral_discover" {
  project      = var.project_id
  account_id   = "raicentral-discover"
  display_name = "RAICentral discovery"
}

resource "google_service_account" "raicentral_enforce" {
  count        = var.enable_enforcement_sa ? 1 : 0
  project      = var.project_id
  account_id   = "raicentral-enforce"
  display_name = "RAICentral enforcement"
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

resource "google_project_iam_member" "enforce_model_armor" {
  count   = var.enable_enforcement_sa ? 1 : 0
  project = var.project_id
  role    = "roles/modelarmor.admin"
  member  = "serviceAccount:${google_service_account.raicentral_enforce[0].email}"
}
