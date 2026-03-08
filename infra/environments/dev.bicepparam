// ---------------------------------------------------------------------------
// Environment Parameters: dev
// Project: Surf — Multi-Agent Orchestration Platform
// Description: Smallest SKUs, serverless where possible
// ---------------------------------------------------------------------------

using '../main.bicep'

param environmentName = 'dev'
param location = 'australiaeast'
param projectName = 'surf'

// AI Search — smallest tier
param aiSearchSku = 'basic'
param aiSearchReplicaCount = 1
param aiSearchPartitionCount = 1

// Cosmos DB — serverless for dev (pay-per-request)
param cosmosCapacityMode = 'Serverless'
param cosmosThroughput = 400

// Storage — locally redundant
param storageSku = 'Standard_LRS'

// Key Vault — standard, no purge protection in dev
param keyVaultSku = 'standard'
param keyVaultEnablePurgeProtection = false

// Log Analytics — minimum retention
param logAnalyticsRetentionDays = 30

// Container Apps — minimal resources, scale to zero
param acrSku = 'Basic'
param containerAppsCpu = '0.25'
param containerAppsMemory = '0.5Gi'
param apiMinReplicas = 0
param apiMaxReplicas = 1
param ingestionMinReplicas = 0
param ingestionMaxReplicas = 1

// OpenAI — embeddings only (chat uses Anthropic Claude directly)
param embeddingCapacity = 10

// ACR — allow public access so GitHub-hosted runners can push images
param acrPublicNetworkAccess = 'Enabled'
