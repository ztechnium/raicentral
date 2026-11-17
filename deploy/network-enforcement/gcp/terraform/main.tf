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

# Workload egress rules live in firewall.tf (ILB IP + enforce toggle).

resource "google_compute_address" "gateway_ilb" {
  name         = "rai-gateway-ilb-ip"
  address_type = "INTERNAL"
  purpose      = "GCE_ENDPOINT"
  subnetwork   = google_compute_subnetwork.gateway.id
  region       = var.region
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
    port = var.gateway_listen_port
  }
}

resource "google_compute_forwarding_rule" "gateway_ilb" {
  name                  = "rai-gateway-ilb-forwarding"
  region                = var.region
  load_balancing_scheme = "INTERNAL"
  backend_service       = google_compute_region_backend_service.gateway_ilb.id
  ip_address            = google_compute_address.gateway_ilb.id
  ip_protocol           = "TCP"
  ports                 = [tostring(var.gateway_listen_port)]
  network               = google_compute_network.rai.id
  subnetwork            = google_compute_subnetwork.gateway.id
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

  metadata = {
    rai-gateway-image = var.gateway_container_image
  }

  metadata_startup_script = file("${path.module}/../startup/gateway-startup.sh")
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