output "discover_service_account_email" {
  value = google_service_account.raicentral_discover.email
}

output "enforce_service_account_email" {
  value = var.enable_enforcement_sa ? google_service_account.raicentral_enforce[0].email : null
}

output "workload_identity_pool" {
  value = var.wif_issuer_uri != "" ? google_iam_workload_identity_pool.raicentral[0].name : null
}

output "workload_identity_provider" {
  value = var.wif_issuer_uri != "" ? google_iam_workload_identity_pool_provider.raicentral[0].name : null
}
