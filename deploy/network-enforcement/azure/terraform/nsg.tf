locals {
  gateway_host_octet = cidrhost(var.gateway_address_prefix, 4)
}

resource "azurerm_network_security_group" "workload" {
  name                = "rai-workload-nsg"
  location            = azurerm_resource_group.rai.location
  resource_group_name = azurerm_resource_group.rai.name
}

resource "azurerm_subnet_network_security_group_association" "workload" {
  subnet_id                 = azurerm_subnet.workload.id
  network_security_group_id = azurerm_network_security_group.workload.id
}

resource "azurerm_network_security_rule" "workload_to_gateway" {
  name                        = "AllowGatewayHttps"
  priority                    = 100
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefix       = var.spoke_address_prefix
  destination_address_prefix  = local.gateway_host_octet
  resource_group_name         = azurerm_resource_group.rai.name
  network_security_group_name = azurerm_network_security_group.workload.name
}

resource "azurerm_network_security_rule" "workload_deny_internet" {
  count = var.enable_enforce_nsg ? 1 : 0

  name                        = "DenyInternetHttps"
  priority                    = 200
  direction                   = "Outbound"
  access                      = "Deny"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefix       = var.spoke_address_prefix
  destination_address_prefix  = "Internet"
  resource_group_name         = azurerm_resource_group.rai.name
  network_security_group_name = azurerm_network_security_group.workload.name
}

resource "azurerm_network_security_group" "gateway" {
  name                = "rai-gateway-nsg"
  location            = azurerm_resource_group.rai.location
  resource_group_name = azurerm_resource_group.rai.name
}

resource "azurerm_subnet_network_security_group_association" "gateway" {
  subnet_id                 = azurerm_subnet.gateway.id
  network_security_group_id = azurerm_network_security_group.gateway.id
}

resource "azurerm_network_security_rule" "gateway_egress_https" {
  name                        = "AllowProviderHttps"
  priority                    = 100
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefix       = var.gateway_address_prefix
  destination_address_prefix  = "AzureCloud"
  resource_group_name         = azurerm_resource_group.rai.name
  network_security_group_name = azurerm_network_security_group.gateway.name
}