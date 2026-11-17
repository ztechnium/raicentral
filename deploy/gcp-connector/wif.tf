resource "google_iam_workload_identity_pool" "raicentral" {
  count                     = var.wif_issuer_uri != "" ? 1 : 0
  project                   = var.project_id
  workload_identity_pool_id = var.wif_pool_id
  display_name              = "RAICentral pool"
}

resource "google_iam_workload_identity_pool_provider" "raicentral" {
  count                              = var.wif_issuer_uri != "" ? 1 : 0
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.raicentral[0].workload_identity_pool_id
  workload_identity_pool_provider_id = var.wif_provider_id
  display_name                       = "RAICentral OIDC"
  attribute_mapping = {
    "google.subject" = "assertion.sub"
  }
  oidc {
    issuer_uri = var.wif_issuer_uri
  }
}

resource "google_service_account_iam_member" "discover_wif_user" {
  count              = var.wif_issuer_uri != "" ? 1 : 0
  service_account_id = google_service_account.raicentral_discover.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${var.wif_pool_id}/*"
}

data "google_project" "current" {
  project_id = var.project_id
}
