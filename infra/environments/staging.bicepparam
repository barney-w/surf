// ---------------------------------------------------------------------------
// Environment Parameters: staging
// Project: Surf — Multi-Agent Orchestration Platform
// Description: Mirrors prod SKUs at lower scale
// ---------------------------------------------------------------------------

using '../main.bicep'

param environmentName = 'staging'
param location = 'australiaeast'
param projectName = 'surf'

// AI Search — standard tier (same as prod), single replica
param aiSearchSku = 'standard'
param aiSearchReplicaCount = 1
param aiSearchPartitionCount = 1

// Cosmos DB — provisioned (matches prod mode) at lower throughput
param cosmosCapacityMode = 'Provisioned'
param cosmosThroughput = 400

// Storage — geo-redundant (matches prod tier)
param storageSku = 'Standard_GRS'

// Key Vault — standard with purge protection
param keyVaultSku = 'standard'
param keyVaultEnablePurgeProtection = true

// Log Analytics — moderate retention
param logAnalyticsRetentionDays = 60

// Container Apps — moderate resources
param acrSku = 'Standard'
param containerAppsCpu = '0.5'
param containerAppsMemory = '1Gi'
param apiMinReplicas = 1
param apiMaxReplicas = 3
param ingestionMinReplicas = 0
param ingestionMaxReplicas = 1

// OpenAI — moderate capacity
param gpt4oCapacity = 30
param embeddingCapacity = 20
