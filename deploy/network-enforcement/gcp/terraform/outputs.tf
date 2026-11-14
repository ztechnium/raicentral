output "gateway_subnet" {
  value = google_compute_subnetwork.gateway.name
}

output "workload_subnet" {
  value = google_compute_subnetwork.workload.name
}

output "gateway_service_account" {
  value = google_service_account.gateway.email
}