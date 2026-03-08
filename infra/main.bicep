// ---------------------------------------------------------------------------
// Orchestrator: main.bicep
// Project: Surf — Multi-Agent Orchestration Platform
// Description: Deploys all infrastructure modules for the Surf platform.
// ---------------------------------------------------------------------------

targetScope = 'resourceGroup'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Environment name')
@allowed(['dev', 'staging', 'prod'])
param environmentName string

@description('Azure region for all resources')
param location string = 'australiaeast'

@description('Project name used as a naming prefix')
param projectName string = 'surf'

// Module-specific SKU / capacity parameters
@description('Azure AI Search SKU')
param aiSearchSku string = 'basic'

@description('Azure AI Search replica count')
param aiSearchReplicaCount int = 1

@description('Azure AI Search partition count')
param aiSearchPartitionCount int = 1

@description('Cosmos DB capacity mode')
@allowed(['Serverless', 'Provisioned'])
param cosmosCapacityMode string = 'Serverless'

@description('Cosmos DB provisioned throughput (RU/s)')
param cosmosThroughput int = 400

@description('Storage account SKU')
param storageSku string = 'Standard_LRS'

@description('Key Vault SKU')
param keyVaultSku string = 'standard'

@description('Key Vault enable purge protection')
param keyVaultEnablePurgeProtection bool = false

@description('Allow public network access to Key Vault')
@allowed(['Enabled', 'Disabled'])
param keyVaultPublicNetworkAccess string = 'Enabled'

@description('Log Analytics retention in days')
param logAnalyticsRetentionDays int = 30

@description('ACR SKU')
param acrSku string = 'Basic'

@description('Allow public network access to ACR (needed for GitHub-hosted runners in dev)')
@allowed(['Enabled', 'Disabled'])
param acrPublicNetworkAccess string = 'Disabled'

@description('Container Apps CPU cores')
param containerAppsCpu string = '0.5'

@description('Container Apps memory')
param containerAppsMemory string = '1Gi'

@description('API minimum replicas')
param apiMinReplicas int = 0

@description('API maximum replicas')
param apiMaxReplicas int = 3

@description('Ingestion minimum replicas')
param ingestionMinReplicas int = 0

@description('Ingestion maximum replicas')
param ingestionMaxReplicas int = 1

@description('Embedding model capacity')
param embeddingCapacity int = 10

@secure()
@description('Anthropic API key (stored in Key Vault, passed to surf-api container)')
param anthropicApiKey string = ''

@description('Anthropic model ID for chat agents')
param anthropicModelId string = 'claude-sonnet-4-6'

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------

var baseName = '${projectName}-${environmentName}'
var tags = {
  project: projectName
  environment: environmentName
}

// Unique suffix for globally-unique names
var uniqueSuffix = uniqueString(resourceGroup().id, projectName, environmentName)

// ---------------------------------------------------------------------------
// Module: Log Analytics
// ---------------------------------------------------------------------------

module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'deploy-log-analytics'
  params: {
    workspaceName: 'log-${baseName}'
    location: location
    retentionInDays: logAnalyticsRetentionDays
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Module: Azure OpenAI (embeddings only — chat uses Anthropic Claude directly)
// ---------------------------------------------------------------------------

module openAi 'modules/openai.bicep' = {
  name: 'deploy-openai'
  params: {
    openAiName: 'oai-${baseName}'
    location: location
    deployGpt52: false
    embeddingCapacity: embeddingCapacity
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Module: Azure AI Search
// ---------------------------------------------------------------------------

module aiSearch 'modules/ai-search.bicep' = {
  name: 'deploy-ai-search'
  params: {
    searchName: 'search-${baseName}'
    location: location
    skuName: aiSearchSku
    replicaCount: aiSearchReplicaCount
    partitionCount: aiSearchPartitionCount
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Module: Key Vault
// ---------------------------------------------------------------------------

module keyVault 'modules/key-vault.bicep' = {
  name: 'deploy-key-vault'
  params: {
    keyVaultName: 'kv-${baseName}-${take(uniqueSuffix, 10)}'
    location: location
    skuName: keyVaultSku
    enablePurgeProtection: keyVaultEnablePurgeProtection
    publicNetworkAccess: keyVaultPublicNetworkAccess
    anthropicApiKey: anthropicApiKey
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Module: Storage Account
// ---------------------------------------------------------------------------

module storage 'modules/storage.bicep' = {
  name: 'deploy-storage'
  params: {
    storageAccountName: 'st${projectName}${environmentName}${uniqueSuffix}'
    location: location
    skuName: storageSku
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Module: Cosmos DB
// ---------------------------------------------------------------------------

module cosmosDb 'modules/cosmos-db.bicep' = {
  name: 'deploy-cosmos-db'
  params: {
    cosmosAccountName: 'cosmos-${baseName}'
    location: location
    capacityMode: cosmosCapacityMode
    throughput: cosmosThroughput
    databaseName: 'surf'
    containerName: 'conversations'
    partitionKeyPath: '/user_id'
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Module: Networking (VNet + Subnets + Private Endpoints)
// ---------------------------------------------------------------------------

module networking 'modules/networking.bicep' = {
  name: 'deploy-networking'
  params: {
    vnetName: 'vnet-${baseName}'
    location: location
    aiSearchId: aiSearch.outputs.searchId
    cosmosDbId: cosmosDb.outputs.cosmosAccountId
    storageAccountId: storage.outputs.storageAccountId
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Module: Container Apps (Environment + Apps + ACR)
// ---------------------------------------------------------------------------

module containerApps 'modules/container-apps.bicep' = {
  name: 'deploy-container-apps'
  params: {
    baseName: baseName
    location: location
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
    containerAppsSubnetId: networking.outputs.containerAppsSubnetId
    acrSku: acrSku
    acrPublicNetworkAccess: acrPublicNetworkAccess
    cpuCores: containerAppsCpu
    memorySize: containerAppsMemory
    apiMinReplicas: apiMinReplicas
    apiMaxReplicas: apiMaxReplicas
    ingestionMinReplicas: ingestionMinReplicas
    ingestionMaxReplicas: ingestionMaxReplicas
    openAiEndpoint: openAi.outputs.openAiEndpoint
    aiSearchEndpoint: aiSearch.outputs.searchEndpoint
    cosmosEndpoint: cosmosDb.outputs.cosmosEndpoint
    storageBlobEndpoint: storage.outputs.blobEndpoint
    keyVaultUri: keyVault.outputs.keyVaultUri
    keyVaultName: keyVault.outputs.keyVaultName
    anthropicModelId: anthropicModelId
    anthropicApiKeyExists: !empty(anthropicApiKey)
    openAiId: openAi.outputs.openAiId
    aiSearchId: aiSearch.outputs.searchId
    cosmosAccountId: cosmosDb.outputs.cosmosAccountId
    storageAccountId: storage.outputs.storageAccountId
    keyVaultId: keyVault.outputs.keyVaultId
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Container Apps Environment name')
output containerAppsEnvName string = containerApps.outputs.containerAppsEnvName

@description('ACR login server')
output acrLoginServer string = containerApps.outputs.acrLoginServer

@description('Azure OpenAI endpoint')
output openAiEndpoint string = openAi.outputs.openAiEndpoint

@description('Azure AI Search endpoint')
output aiSearchEndpoint string = aiSearch.outputs.searchEndpoint

@description('Cosmos DB endpoint')
output cosmosEndpoint string = cosmosDb.outputs.cosmosEndpoint

@description('Key Vault URI')
output keyVaultUri string = keyVault.outputs.keyVaultUri

@description('Storage blob endpoint')
output storageBlobEndpoint string = storage.outputs.blobEndpoint

@description('VNet name')
output vnetName string = networking.outputs.vnetName
