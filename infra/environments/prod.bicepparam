// ---------------------------------------------------------------------------
// Environment Parameters: prod
// Project: Surf — Multi-Agent Orchestration Platform
// Description: Production-grade SKUs with high availability
// ---------------------------------------------------------------------------

using '../main.bicep'

param environmentName = 'prod'
param location = 'australiaeast'
param projectName = 'surf'

// AI Search — standard tier with multiple replicas for HA
param aiSearchSku = 'standard'
param aiSearchReplicaCount = 3
param aiSearchPartitionCount = 1

// Cosmos DB — provisioned with production throughput
param cosmosCapacityMode = 'Provisioned'
param cosmosThroughput = 1000

// Storage — geo-redundant with read access
param storageSku = 'Standard_RAGRS'

// Key Vault — standard with purge protection enabled
param keyVaultSku = 'standard'
param keyVaultEnablePurgeProtection = true

// Log Analytics — extended retention for compliance
param logAnalyticsRetentionDays = 90

// Container Apps — production resources, always-on
param acrSku = 'Premium'
param containerAppsCpu = '1'
param containerAppsMemory = '2Gi'
param apiMinReplicas = 2
param apiMaxReplicas = 10
param ingestionMinReplicas = 1
param ingestionMaxReplicas = 3

// OpenAI — embeddings only (chat uses Anthropic Claude directly)
param embeddingCapacity = 50
