output "hub_vnet_id" {
  value = azurerm_virtual_network.hub.id
}

output "spoke_vnet_id" {
  value = azurerm_virtual_network.spoke.id
}

output "gateway_managed_identity" {
  value = azurerm_user_assigned_identity.gateway.client_id
}