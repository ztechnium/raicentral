# Workload → gateway only (HTTPS to ILB). Workload → internet denied in ENFORCE.
resource "google_compute_firewall" "workload_to_gateway" {
  name    = "rai-workload-to-gateway"
  network = google_compute_network.rai.name

  direction = "EGRESS"
  priority  = 1000

  destination_ranges = ["${google_compute_address.gateway_ilb.address}/32"]
  target_tags        = ["rai-workload"]

  allow {
    protocol = "tcp"
    ports    = ["443", "8000"]
  }
}

resource "google_compute_firewall" "workload_deny_internet_https" {
  count = var.enable_enforce_firewall ? 1 : 0

  name    = "rai-workload-deny-internet-https"
  network = google_compute_network.rai.name

  direction = "EGRESS"
  priority  = 2000

  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["rai-workload"]

  deny {
    protocol = "tcp"
    ports    = ["443"]
  }
}

# Gateway → providers via NAT (tag rai-gateway on gateway VMs / node pool)
resource "google_compute_firewall" "gateway_egress_https" {
  name    = "rai-gateway-egress-https"
  network = google_compute_network.rai.name

  direction = "EGRESS"
  priority  = 1000

  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["rai-gateway"]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
}