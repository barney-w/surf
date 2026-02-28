// ---------------------------------------------------------------------------
// Dev-Local Infrastructure: dev-local.bicep
// Project: Surf — Multi-Agent Orchestration Platform
// Description: Minimal, publicly-accessible Azure resources for local dev.
//              Deploy with: just setup-dev
//              Destroy with: just teardown-dev
// ---------------------------------------------------------------------------

targetScope = 'resourceGroup'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Azure region for all resources (OpenAI is deployed separately to eastus)')
param location string = 'australiaeast'

@description('Object ID of the signed-in user (for RBAC). Get with: az ad signed-in-user show --query id -o tsv')
param userObjectId string

@description('Project name used as a naming prefix')
param projectName string = 'surf'

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------

var uniqueSuffix = uniqueString(resourceGroup().id, projectName)
var tags = {
  project: projectName
  environment: 'dev-local'
}


// ---------------------------------------------------------------------------
// Azure Cosmos DB (Serverless)
// ---------------------------------------------------------------------------

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'cosmos-${projectName}-dev-${uniqueSuffix}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: 'surf'
  properties: {
    resource: {
      id: 'surf'
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'conversations'
  properties: {
    resource: {
      id: 'conversations'
      partitionKey: {
        paths: [
          '/user_id'
        ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
        excludedPaths: [
          {
            path: '/_etag/?'
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Azure AI Search (Basic)
// ---------------------------------------------------------------------------

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: 'search-${projectName}-dev-${uniqueSuffix}'
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Azure Storage Account
// ---------------------------------------------------------------------------

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${projectName}dev${uniqueSuffix}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
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
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
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
// RBAC Role Assignments
// ---------------------------------------------------------------------------

// Cosmos DB Built-in Data Contributor → Cosmos account
// Note: This uses the Cosmos DB data-plane RBAC (SQL role), not ARM RBAC.
resource cosmosRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, userObjectId, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: userObjectId
    scope: cosmosAccount.id
  }
}

// Search Index Data Contributor → AI Search
resource searchRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, userObjectId, 'search-index-data-contributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalId: userObjectId
    principalType: 'User'
  }
}

// Search Service Contributor → AI Search (needed to create/manage indexes)
resource searchServiceRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, userObjectId, 'search-service-contributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
    principalId: userObjectId
    principalType: 'User'
  }
}

// Storage Blob Data Contributor → Storage account
resource storageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, userObjectId, 'storage-blob-data-contributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: userObjectId
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------------
// Outputs (used by setup-dev to generate .env)
// ---------------------------------------------------------------------------

@description('Azure AI Search endpoint')
output searchEndpoint string = 'https://${search.name}.search.windows.net'

@description('Cosmos DB endpoint')
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint

@description('Storage blob endpoint')
output storageBlobEndpoint string = storageAccount.properties.primaryEndpoints.blob
