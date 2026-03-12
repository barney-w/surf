targetScope = 'resourceGroup'

@description('Azure region for all resources (OpenAI is deployed separately to eastus)')
param location string = 'australiaeast'

@description('Object ID of the signed-in user (for RBAC)')
param userObjectId string

@description('Project name used as a naming prefix')
param projectName string = 'surf'

var uniqueSuffix = uniqueString(resourceGroup().id, projectName)
var tags = {
  project: projectName
  environment: 'dev-local'
}

module search 'br/public:avm/res/search/search-service:0.12.0' = {
  name: 'deploy-search'
  params: {
    name: 'search-${projectName}-dev-${uniqueSuffix}'
    location: location
    tags: tags
    sku: 'basic'
    replicaCount: 1
    partitionCount: 1
    publicNetworkAccess: 'Enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
    roleAssignments: [
      {
        principalId: userObjectId
        roleDefinitionIdOrName: 'Search Index Data Contributor'
        principalType: 'User'
      }
      {
        principalId: userObjectId
        roleDefinitionIdOrName: 'Search Service Contributor'
        principalType: 'User'
      }
    ]
  }
}

module storageAccount 'br/public:avm/res/storage/storage-account:0.14.3' = {
  name: 'deploy-storage'
  params: {
    name: 'st${projectName}dev${uniqueSuffix}'
    location: location
    tags: tags
    skuName: 'Standard_LRS'
    kind: 'StorageV2'
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    blobServices: {
      containers: [
        { name: 'documents', publicAccess: 'None' }
        { name: 'ingested', publicAccess: 'None' }
      ]
    }
    roleAssignments: [
      {
        principalId: userObjectId
        roleDefinitionIdOrName: 'Storage Blob Data Contributor'
        principalType: 'User'
      }
    ]
  }
}

@description('Azure AI Search endpoint')
output searchEndpoint string = search.outputs.endpoint

@description('Storage blob endpoint')
output storageBlobEndpoint string = storageAccount.outputs.primaryBlobEndpoint
