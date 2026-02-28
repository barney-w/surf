// ---------------------------------------------------------------------------
// Module: Azure Cosmos DB
// Project: Surf — Multi-Agent Orchestration Platform
// ---------------------------------------------------------------------------

@description('Name of the Cosmos DB account')
param cosmosAccountName string

@description('Azure region for deployment')
param location string

@description('Tags to apply to all resources')
param tags object

@description('Capacity mode: Serverless or Provisioned')
@allowed(['Serverless', 'Provisioned'])
param capacityMode string = 'Serverless'

@description('Provisioned throughput (RU/s) — only used when capacityMode is Provisioned')
param throughput int = 400

@description('Enable multi-region writes')
param enableMultipleWriteLocations bool = false

@description('Database name')
param databaseName string = 'surf'

@description('Container name')
param containerName string = 'conversations'

@description('Partition key path')
param partitionKeyPath string = '/user_id'

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosAccountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  identity: {
    type: 'SystemAssigned'
  }
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
    capabilities: capacityMode == 'Serverless'
      ? [
          {
            name: 'EnableServerless'
          }
        ]
      : []
    enableMultipleWriteLocations: enableMultipleWriteLocations
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: databaseName
  tags: tags
  properties: {
    resource: {
      id: databaseName
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: containerName
  tags: tags
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        paths: [
          partitionKeyPath
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
    options: capacityMode == 'Provisioned'
      ? {
          throughput: throughput
        }
      : {}
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Resource ID of the Cosmos DB account')
output cosmosAccountId string = cosmosAccount.id

@description('Name of the Cosmos DB account')
output cosmosAccountName string = cosmosAccount.name

@description('Endpoint of the Cosmos DB account')
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint

@description('Principal ID of the managed identity')
output cosmosPrincipalId string = cosmosAccount.identity.principalId

@description('Database name')
output cosmosDatabaseName string = database.name

@description('Container name')
output cosmosContainerName string = container.name
