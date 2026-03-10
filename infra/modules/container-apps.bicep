// ---------------------------------------------------------------------------
// Module: Container Apps Environment, Apps, and ACR
// Project: Surf — Multi-Agent Orchestration Platform
// ---------------------------------------------------------------------------

@description('Base name for container apps resources')
param baseName string

@description('Azure region for deployment')
param location string

@description('Tags to apply to all resources')
param tags object

@description('Resource ID of the Log Analytics workspace')
param logAnalyticsWorkspaceId string

@description('Resource ID of the Container Apps subnet')
param containerAppsSubnetId string

@description('ACR SKU')
@allowed(['Basic', 'Standard', 'Premium'])
param acrSku string = 'Basic'

@description('Allow public network access to ACR (needed for GitHub-hosted runners in dev)')
@allowed(['Enabled', 'Disabled'])
param acrPublicNetworkAccess string = 'Disabled'

@description('Container app CPU cores')
param cpuCores string = '0.5'

@description('Container app memory')
param memorySize string = '1Gi'

@description('Minimum replicas for surf-api')
param apiMinReplicas int = 0

@description('Maximum replicas for surf-api')
param apiMaxReplicas int = 3

@description('Minimum replicas for surf-ingestion')
param ingestionMinReplicas int = 0

@description('Maximum replicas for surf-ingestion')
param ingestionMaxReplicas int = 1

// Downstream resource details for environment variables
@description('Azure OpenAI endpoint')
param openAiEndpoint string = ''

@description('Azure AI Search endpoint')
param aiSearchEndpoint string = ''

@description('Azure AI Search SharePoint index name (empty to disable)')
param aiSearchSharepointIndex string = ''

@description('Cosmos DB endpoint')
param cosmosEndpoint string = ''

@description('Storage blob endpoint')
param storageBlobEndpoint string = ''

@description('Key Vault URI')
param keyVaultUri string = ''

@description('Key Vault name (for secret references)')
param keyVaultName string = ''

@description('Anthropic model ID for chat agents')
param anthropicModelId string = 'claude-sonnet-4-6'

// Resource IDs for role assignments
@description('Azure OpenAI resource ID')
param openAiId string = ''

@description('Azure AI Search resource ID')
param aiSearchId string = ''

@description('Cosmos DB account ID')
param cosmosAccountId string = ''

@description('Storage account ID')
param storageAccountId string = ''

@description('Key Vault ID')
param keyVaultId string = ''

@description('Container image tag for surf-api (e.g. git SHA). Leave empty to use a placeholder image on first bootstrap.')
param apiImageTag string = ''

@description('Container image tag for surf-ingestion (e.g. git SHA). Leave empty to use a placeholder image on first bootstrap.')
param ingestionImageTag string = ''

@description('Whether the anthropic-api-key secret exists in Key Vault. Set false on first bootstrap if the key has not been stored yet.')
param anthropicApiKeyExists bool = false

// ---------------------------------------------------------------------------
// Existing resource references
// ---------------------------------------------------------------------------

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: last(split(logAnalyticsWorkspaceId, '/'))
}

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------

var acrName = replace('acr${baseName}', '-', '')

// Use a public placeholder image on first bootstrap when no tag has been pushed to ACR yet
var bootstrapImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var apiImage = !empty(apiImageTag) ? '${acr.properties.loginServer}/surf-api:${apiImageTag}' : bootstrapImage
var ingestionImage = !empty(ingestionImageTag) ? '${acr.properties.loginServer}/surf-ingestion:${ingestionImageTag}' : bootstrapImage

// ---------------------------------------------------------------------------
// Azure Container Registry
// ---------------------------------------------------------------------------

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: acrSku
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: acrPublicNetworkAccess
  }
}

// ---------------------------------------------------------------------------
// Container Apps Environment
// ---------------------------------------------------------------------------

resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${baseName}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: containerAppsSubnetId
      internal: true
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Managed Identity for Container Apps
// ---------------------------------------------------------------------------

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${baseName}'
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------
// Existing resource references for role assignment scoping
// ---------------------------------------------------------------------------

resource existingOpenAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = if (!empty(openAiId)) {
  name: last(split(openAiId, '/'))
}

resource existingAiSearch 'Microsoft.Search/searchServices@2023-11-01' existing = if (!empty(aiSearchId)) {
  name: last(split(aiSearchId, '/'))
}

resource existingCosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = if (!empty(cosmosAccountId)) {
  name: last(split(cosmosAccountId, '/'))
}

resource existingStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = if (!empty(storageAccountId)) {
  name: last(split(storageAccountId, '/'))
}

resource existingKeyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = if (!empty(keyVaultId)) {
  name: last(split(keyVaultId, '/'))
}

// ---------------------------------------------------------------------------
// Role Assignments — Managed Identity access to downstream resources
// ---------------------------------------------------------------------------

// Cognitive Services OpenAI User role on OpenAI resource
resource roleOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(openAiId)) {
  name: guid(managedIdentity.id, openAiId, 'cognitive-services-openai-user')
  scope: existingOpenAi
  properties: {
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' // Cognitive Services OpenAI User
    )
  }
}

// Search Index Data Contributor on AI Search
resource roleSearchContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(aiSearchId)) {
  name: guid(managedIdentity.id, aiSearchId, 'search-index-data-contributor')
  scope: existingAiSearch
  properties: {
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor
    )
  }
}

// Cosmos DB Built-in Data Contributor (data plane role — must use sqlRoleAssignments, not ARM roleAssignments)
resource roleCosmosContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = if (!empty(cosmosAccountId)) {
  name: guid(managedIdentity.id, cosmosAccountId, 'cosmos-db-data-contributor')
  parent: existingCosmos
  properties: {
    principalId: managedIdentity.properties.principalId
    roleDefinitionId: '${existingCosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    scope: existingCosmos.id
  }
}

// Storage Blob Data Contributor
resource roleStorageContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(storageAccountId)) {
  name: guid(managedIdentity.id, storageAccountId, 'storage-blob-data-contributor')
  scope: existingStorage
  properties: {
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe' // Storage Blob Data Contributor
    )
  }
}

// Key Vault Secrets User
resource roleKeyVaultUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(keyVaultId)) {
  name: guid(managedIdentity.id, keyVaultId, 'key-vault-secrets-user')
  scope: existingKeyVault
  properties: {
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User
    )
  }
}

// ACR Pull for managed identity
resource roleAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(managedIdentity.id, acr.id, 'acr-pull')
  scope: acr
  properties: {
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d' // AcrPull
    )
  }
}

// ---------------------------------------------------------------------------
// Container App: surf-api
// ---------------------------------------------------------------------------

resource surfApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-api-${baseName}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: (!empty(keyVaultName) && anthropicApiKeyExists) ? [
        {
          name: 'anthropic-api-key'
          keyVaultUrl: '${keyVaultUri}secrets/anthropic-api-key'
          identity: managedIdentity.id
        }
      ] : []
      registries: [
        {
          server: acr.properties.loginServer
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'surf-api'
          image: apiImage
          resources: {
            cpu: json(cpuCores)
            memory: memorySize
          }
          env: concat([
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
            { name: 'AZURE_SEARCH_ENDPOINT', value: aiSearchEndpoint }
            { name: 'AZURE_SEARCH_SHAREPOINT_INDEX', value: aiSearchSharepointIndex }
            { name: 'AZURE_COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: storageBlobEndpoint }
            { name: 'AZURE_KEY_VAULT_URI', value: keyVaultUri }
            { name: 'AZURE_CLIENT_ID', value: managedIdentity.properties.clientId }
            { name: 'ANTHROPIC_MODEL_ID', value: anthropicModelId }
          ], anthropicApiKeyExists ? [{ name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }] : [])
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/v1/health'
                port: 8000
              }
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Startup'
              httpGet: {
                path: '/api/v1/health'
                port: 8000
              }
              periodSeconds: 10
              failureThreshold: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    roleAcrPull
  ]
}

// ---------------------------------------------------------------------------
// Container App: surf-ingestion
// ---------------------------------------------------------------------------

resource surfIngestion 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-ingestion-${baseName}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: acr.properties.loginServer
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'surf-ingestion'
          image: ingestionImage
          resources: {
            cpu: json(cpuCores)
            memory: memorySize
          }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
            { name: 'AZURE_SEARCH_ENDPOINT', value: aiSearchEndpoint }
            { name: 'AZURE_COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: storageBlobEndpoint }
            { name: 'AZURE_KEY_VAULT_URI', value: keyVaultUri }
            { name: 'AZURE_CLIENT_ID', value: managedIdentity.properties.clientId }
          ]
        }
      ]
      scale: {
        minReplicas: ingestionMinReplicas
        maxReplicas: ingestionMaxReplicas
      }
    }
  }
  dependsOn: [
    roleAcrPull
  ]
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Resource ID of the Container Apps Environment')
output containerAppsEnvId string = containerAppsEnv.id

@description('Name of the Container Apps Environment')
output containerAppsEnvName string = containerAppsEnv.name

@description('FQDN of the surf-api container app')
output surfApiFqdn string = surfApi.properties.configuration.ingress.fqdn

@description('Resource ID of the ACR')
output acrId string = acr.id

@description('ACR login server')
output acrLoginServer string = acr.properties.loginServer

@description('Principal ID of the user-assigned managed identity')
output managedIdentityPrincipalId string = managedIdentity.properties.principalId

@description('Client ID of the user-assigned managed identity')
output managedIdentityClientId string = managedIdentity.properties.clientId

@description('Resource ID of the user-assigned managed identity')
output managedIdentityId string = managedIdentity.id
