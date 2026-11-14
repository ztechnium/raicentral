terraform {
  required_version = ">= 1.5.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}

resource "azurerm_resource_group" "rai" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_virtual_network" "hub" {
  name                = "rai-hub"
  location            = azurerm_resource_group.rai.location
  resource_group_name = azurerm_resource_group.rai.name
  address_space       = [var.hub_address_prefix]
}

resource "azurerm_virtual_network" "spoke" {
  name                = "rai-spoke"
  location            = azurerm_resource_group.rai.location
  resource_group_name = azurerm_resource_group.rai.name
  address_space       = [var.spoke_address_prefix, var.gateway_address_prefix]
}

resource "azurerm_subnet" "workload" {
  name                 = "rai-workload"
  resource_group_name  = azurerm_resource_group.rai.name
  virtual_network_name = azurerm_virtual_network.spoke.name
  address_prefixes     = [var.spoke_address_prefix]
}

resource "azurerm_subnet" "gateway" {
  name                 = "rai-gateway"
  resource_group_name  = azurerm_resource_group.rai.name
  virtual_network_name = azurerm_virtual_network.spoke.name
  address_prefixes     = [var.gateway_address_prefix]
}

resource "azurerm_virtual_network_peering" "hub_spoke" {
  name                      = "hub-to-spoke"
  resource_group_name       = azurerm_resource_group.rai.name
  virtual_network_name      = azurerm_virtual_network.hub.name
  remote_virtual_network_id = azurerm_virtual_network.spoke.id
  allow_forwarded_traffic   = true
}

resource "azurerm_virtual_network_peering" "spoke_hub" {
  name                      = "spoke-to-hub"
  resource_group_name       = azurerm_resource_group.rai.name
  virtual_network_name      = azurerm_virtual_network.spoke.name
  remote_virtual_network_id = azurerm_virtual_network.hub.id
  allow_forwarded_traffic   = true
}