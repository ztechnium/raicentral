terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_compute_network" "rai" {
  name                    = "rai-enforcement"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "gateway" {
  name          = "rai-gateway"
  ip_cidr_range = var.gateway_subnet_cidr
  region        = var.region
  network       = google_compute_network.rai.id
}

resource "google_compute_subnetwork" "workload" {
  name          = "rai-workloads"
  ip_cidr_range = var.workload_subnet_cidr
  region        = var.region
  network       = google_compute_network.rai.id
}

resource "google_service_account" "gateway" {
  account_id   = var.gateway_service_account_id
  display_name = "RaiCentral enforcement gateway"
}

resource "google_compute_router" "rai" {
  name    = "rai-router"
  region  = var.region
  network = google_compute_network.rai.id
}

resource "google_compute_router_nat" "rai" {
  name                               = "rai-nat"
  router                             = google_compute_router.rai.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = google_compute_subnetwork.gateway.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
}

# Workloads: only gateway subnet on 8000/443 as needed for health; deny general internet.
resource "google_compute_firewall" "workload_to_gateway" {
  name    = "rai-workload-to-gateway"
  network = google_compute_network.rai.name
  direction = "EGRESS"
  destination_ranges = [var.gateway_subnet_cidr]
  allow {
    protocol = "tcp"
    ports    = ["8000", "443"]
  }
  target_tags = ["rai-workload"]
}

resource "google_compute_firewall" "workload_deny_internet_https" {
  name    = "rai-workload-deny-internet-https"
  network = google_compute_network.rai.name
  priority = 1000
  direction = "EGRESS"
  destination_ranges = ["0.0.0.0/0"]
  deny {
    protocol = "tcp"
    ports    = ["443"]
  }
  target_tags = ["rai-workload"]
}

resource "google_compute_firewall" "gateway_allow_https_internet" {
  name    = "rai-gateway-allow-https-internet"
  network = google_compute_network.rai.name
  direction = "EGRESS"
  destination_ranges = ["0.0.0.0/0"]
  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
  target_service_accounts = [google_service_account.gateway.email]
}

resource "google_compute_region_backend_service" "gateway_ilb" {
  name                  = "rai-gateway-ilb"
  region                = var.region
  load_balancing_scheme = "INTERNAL"
  protocol              = "TCP"
  health_checks         = [google_compute_region_health_check.gateway.id]
  backend {
    group          = google_compute_region_instance_group_manager.gateway.instance_group
    balancing_mode = "CONNECTION"
  }
}

resource "google_compute_region_health_check" "gateway" {
  name   = "rai-gateway-hc"
  region = var.region
  tcp_health_check {
    port = 8000
  }
}

resource "google_compute_instance_template" "gateway" {
  name         = "rai-gateway-template"
  machine_type = "e2-medium"
  tags         = ["rai-gateway"]

  disk {
    source_image = "debian-cloud/debian-12"
    auto_delete  = true
    boot         = true
  }

  network_interface {
    subnetwork = google_compute_subnetwork.gateway.id
  }

  service_account {
    email  = google_service_account.gateway.email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    # Deploy gateway via container runtime — replace with your image orchestration.
    echo "Start RaiCentral gateway container bound to :8000"
  EOT
}

resource "google_compute_region_instance_group_manager" "gateway" {
  name               = "rai-gateway-mig"
  region             = var.region
  base_instance_name = "rai-gateway"
  version {
    instance_template = google_compute_instance_template.gateway.id
  }
  target_size = 2
}