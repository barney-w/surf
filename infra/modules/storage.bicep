// ---------------------------------------------------------------------------
// Module: Azure Storage Account
// Project: Surf — Multi-Agent Orchestration Platform
// ---------------------------------------------------------------------------

@description('Name of the Storage account')
param storageAccountName string

@description('Azure region for deployment')
param location string

@description('Tags to apply to all resources')
param tags object

@description('SKU for the Storage account')
@allowed(['Standard_LRS', 'Standard_GRS', 'Standard_ZRS', 'Standard_RAGRS'])
param skuName string = 'Standard_LRS'

@description('Storage account kind')
@allowed(['StorageV2', 'BlobStorage'])
param kind string = 'StorageV2'

@description('Access tier')
@allowed(['Hot', 'Cool'])
param accessTier string = 'Hot'

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: kind
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: skuName
  }
  properties: {
    accessTier: accessTier
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource documentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'documents'
  properties: {
    publicAccess: 'None'
  }
}

resource ingestedContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'ingested'
  properties: {
    publicAccess: 'None'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Resource ID of the Storage account')
output storageAccountId string = storageAccount.id

@description('Name of the Storage account')
output storageAccountName string = storageAccount.name

@description('Primary blob endpoint')
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob

@description('Principal ID of the managed identity')
output storagePrincipalId string = storageAccount.identity.principalId
