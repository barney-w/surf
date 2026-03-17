// ---------------------------------------------------------------------------
// Module: Azure AI Search
// Project: Surf — Multi-Agent Orchestration Platform
// ---------------------------------------------------------------------------

@description('Name of the Azure AI Search service')
param searchName string

@description('Azure region for deployment')
param location string

@description('Tags to apply to all resources')
param tags object

@description('SKU for Azure AI Search')
@allowed(['free', 'basic', 'standard', 'standard2', 'standard3'])
param skuName string = 'basic'

@description('Number of replicas')
@minValue(1)
@maxValue(12)
param replicaCount int = 1

@description('Number of partitions')
@allowed([1, 2, 3, 4, 6, 12])
param partitionCount int = 1

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: skuName
  }
  properties: {
    replicaCount: replicaCount
    partitionCount: partitionCount
    hostingMode: 'default'
    publicNetworkAccess: 'disabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Resource ID of the Azure AI Search service')
output searchId string = search.id

@description('Name of the Azure AI Search service')
output searchName string = search.name

@description('Endpoint URL of the Azure AI Search service')
output searchEndpoint string = 'https://${search.name}.search.windows.net'

@description('Principal ID of the managed identity')
output searchPrincipalId string = search.identity.principalId
