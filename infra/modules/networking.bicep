// ---------------------------------------------------------------------------
// Module: Networking — VNet, Subnets, Private Endpoints
// Project: Surf — Multi-Agent Orchestration Platform
// ---------------------------------------------------------------------------

@description('Name prefix for networking resources')
param vnetName string

@description('Azure region for deployment')
param location string

@description('Tags to apply to all resources')
param tags object

@description('VNet address prefix')
param vnetAddressPrefix string = '10.0.0.0/16'

@description('Container Apps subnet address prefix')
param containerAppsSubnetPrefix string = '10.0.0.0/23'

@description('Private Endpoints subnet address prefix')
param privateEndpointsSubnetPrefix string = '10.0.2.0/24'

// Resource IDs for private endpoints
@description('Resource ID of the AI Search service (empty to skip private endpoint)')
param aiSearchId string = ''

@description('Resource ID of the Cosmos DB account (empty to skip private endpoint)')
param cosmosDbId string = ''

@description('Resource ID of the Storage account (empty to skip private endpoint)')
param storageAccountId string = ''

// ---------------------------------------------------------------------------
// Network Security Groups
// ---------------------------------------------------------------------------

resource nsgContainerApps 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: 'nsg-${vnetName}-container-apps'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowHTTPSInbound'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      {
        name: 'AllowContainerAppsToPrivateEndpoints'
        properties: {
          priority: 110
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: containerAppsSubnetPrefix
          sourcePortRange: '*'
          destinationAddressPrefix: privateEndpointsSubnetPrefix
          destinationPortRange: '443'
        }
      }
    ]
  }
}

resource nsgPrivateEndpoints 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: 'nsg-${vnetName}-private-endpoints'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowInboundFromContainerApps'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: containerAppsSubnetPrefix
          sourcePortRange: '*'
          destinationAddressPrefix: privateEndpointsSubnetPrefix
          destinationPortRange: '443'
        }
      }
      {
        name: 'DenyAllOtherInbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Virtual Network
// ---------------------------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: 'snet-container-apps'
        properties: {
          addressPrefix: containerAppsSubnetPrefix
          networkSecurityGroup: {
            id: nsgContainerApps.id
          }
          delegations: [
            {
              name: 'delegation-container-apps'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: privateEndpointsSubnetPrefix
          networkSecurityGroup: {
            id: nsgPrivateEndpoints.id
          }
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Private DNS Zones
// ---------------------------------------------------------------------------

resource dnsZoneSearch 'Microsoft.Network/privateDnsZones@2024-06-01' = if (!empty(aiSearchId)) {
  name: 'privatelink.search.windows.net'
  location: 'global'
  tags: tags
}

resource dnsZoneCosmos 'Microsoft.Network/privateDnsZones@2024-06-01' = if (!empty(cosmosDbId)) {
  name: 'privatelink.documents.azure.com'
  location: 'global'
  tags: tags
}

resource dnsZoneStorage 'Microsoft.Network/privateDnsZones@2024-06-01' = if (!empty(storageAccountId)) {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

// ---------------------------------------------------------------------------
// DNS Zone VNet Links
// ---------------------------------------------------------------------------

resource dnsZoneSearchLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (!empty(aiSearchId)) {
  parent: dnsZoneSearch
  name: '${vnetName}-search-link'
  location: 'global'
  tags: tags
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

resource dnsZoneCosmosLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (!empty(cosmosDbId)) {
  parent: dnsZoneCosmos
  name: '${vnetName}-cosmos-link'
  location: 'global'
  tags: tags
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

resource dnsZoneStorageLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (!empty(storageAccountId)) {
  parent: dnsZoneStorage
  name: '${vnetName}-storage-link'
  location: 'global'
  tags: tags
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

// ---------------------------------------------------------------------------
// Private Endpoints
// ---------------------------------------------------------------------------

resource peSearch 'Microsoft.Network/privateEndpoints@2024-01-01' = if (!empty(aiSearchId)) {
  name: 'pe-${vnetName}-search'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: vnet.properties.subnets[1].id
    }
    privateLinkServiceConnections: [
      {
        name: 'plsc-search'
        properties: {
          privateLinkServiceId: aiSearchId
          groupIds: [
            'searchService'
          ]
        }
      }
    ]
  }
}

resource peSearchDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = if (!empty(aiSearchId)) {
  parent: peSearch
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config-search'
        properties: {
          privateDnsZoneId: dnsZoneSearch.id
        }
      }
    ]
  }
}

resource peCosmos 'Microsoft.Network/privateEndpoints@2024-01-01' = if (!empty(cosmosDbId)) {
  name: 'pe-${vnetName}-cosmos'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: vnet.properties.subnets[1].id
    }
    privateLinkServiceConnections: [
      {
        name: 'plsc-cosmos'
        properties: {
          privateLinkServiceId: cosmosDbId
          groupIds: [
            'Sql'
          ]
        }
      }
    ]
  }
}

resource peCosmosDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = if (!empty(cosmosDbId)) {
  parent: peCosmos
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config-cosmos'
        properties: {
          privateDnsZoneId: dnsZoneCosmos.id
        }
      }
    ]
  }
}

resource peStorage 'Microsoft.Network/privateEndpoints@2024-01-01' = if (!empty(storageAccountId)) {
  name: 'pe-${vnetName}-storage'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: vnet.properties.subnets[1].id
    }
    privateLinkServiceConnections: [
      {
        name: 'plsc-storage'
        properties: {
          privateLinkServiceId: storageAccountId
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
}

resource peStorageDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = if (!empty(storageAccountId)) {
  parent: peStorage
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config-storage'
        properties: {
          privateDnsZoneId: dnsZoneStorage.id
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Resource ID of the Virtual Network')
output vnetId string = vnet.id

@description('Name of the Virtual Network')
output vnetName string = vnet.name

@description('Resource ID of the Container Apps subnet')
output containerAppsSubnetId string = vnet.properties.subnets[0].id

@description('Resource ID of the Private Endpoints subnet')
output privateEndpointsSubnetId string = vnet.properties.subnets[1].id
