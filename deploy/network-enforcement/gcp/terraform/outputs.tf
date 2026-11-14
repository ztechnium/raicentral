output "gateway_subnet" {
  value = google_compute_subnetwork.gateway.name
}

output "workload_subnet" {
  value = google_compute_subnetwork.workload.name
}

output "gateway_service_account" {
  value = google_service_account.gateway.email
}

output "gateway_ilb_ip" {
  value       = google_compute_address.gateway_ilb.address
  description = "Internal IP for workload egress to the gateway load balancer"
}

output "gateway_ilb_forwarding_rule" {
  value = google_compute_forwarding_rule.gateway_ilb.name
}