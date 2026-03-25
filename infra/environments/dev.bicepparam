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
param apiMinReplicas = 1
param apiMaxReplicas = 1
param ingestionMinReplicas = 0
param ingestionMaxReplicas = 1

// OpenAI — embeddings only (chat uses Anthropic Claude directly)
param embeddingCapacity = 10

// ACR — allow public access so GitHub-hosted runners can push images
param acrPublicNetworkAccess = 'Enabled'

// Auth — enabled (SSO via Entra ID)
param authEnabled = true
param entraTenantId = '<your-entra-tenant-id>'
param entraClientId = '<your-entra-client-id>'

// Guest auth — anonymous access tokens (stored in Key Vault)
param guestTokenSecretInKv = true

// CORS — allow localhost origins
param apiCorsOrigins = '["http://localhost:3000","http://localhost:3020","https://tauri.localhost","https://chatwith.surf"]'

// Web container — always on (temporary, revert after 2026-03-20)
param webMinReplicas = 1
param webMaxReplicas = 1

// GitLab CI/CD — Workload Identity Federation
param gitlabOidcIssuer = 'https://gitlab.com'
param gitlabProjectPath = 'your-group/surf'
