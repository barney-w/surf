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

// Storage — locally redundant
param storageSku = 'Standard_LRS'

// Key Vault — standard, no purge protection in dev
param keyVaultSku = 'standard'
param keyVaultEnablePurgeProtection = false

// Log Analytics — minimum retention, public access for dev only
param logAnalyticsRetentionDays = 30
param logAnalyticsPublicNetworkAccess = 'Enabled'

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

// Auth — enabled (SSO via Entra ID)
param authEnabled = true
param entraTenantId = '799af2de-e455-499c-babe-71a7929442ca'
param entraClientId = '08ff7e73-6758-4c55-bdb1-cc4f124de8ac'

// CORS — allow localhost origins
param apiCorsOrigins = '["http://localhost:3000","https://tauri.localhost"]'

// Web container — scale to zero in dev
param webMinReplicas = 0
param webMaxReplicas = 1
