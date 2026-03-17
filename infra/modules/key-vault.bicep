// ---------------------------------------------------------------------------
// Module: Azure Key Vault
// Project: Surf — Multi-Agent Orchestration Platform
// ---------------------------------------------------------------------------

@description('Name of the Key Vault')
param keyVaultName string

@description('Azure region for deployment')
param location string

@description('Tags to apply to all resources')
param tags object

@description('SKU for Key Vault')
@allowed(['standard', 'premium'])
param skuName string = 'standard'

@description('Tenant ID for Azure AD')
param tenantId string = subscription().tenantId

@description('Enable soft delete')
param enableSoftDelete bool = true

@description('Soft delete retention in days')
@minValue(7)
@maxValue(90)
param softDeleteRetentionInDays int = 7

@description('Enable purge protection')
param enablePurgeProtection bool = false

@description('Allow public network access (enable for dev so CLI and GitHub Actions can manage secrets)')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccess string = 'Enabled'

@secure()
@description('Anthropic API key (stored as a secret when provided)')
param anthropicApiKey string = ''

@secure()
@description('Anthropic Foundry API key (stored as a secret when provided)')
param anthropicFoundryApiKey string = ''

@secure()
@description('Entra ID client secret for OBO flow (stored as a secret when provided)')
param entraClientSecret string = ''

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: skuName
    }
    tenantId: tenantId
    enableRbacAuthorization: true
    enableSoftDelete: enableSoftDelete
    softDeleteRetentionInDays: softDeleteRetentionInDays
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: publicNetworkAccess
    networkAcls: {
      defaultAction: publicNetworkAccess == 'Enabled' ? 'Allow' : 'Deny'
      bypass: 'AzureServices'
    }
  }
}

// ---------------------------------------------------------------------------
// Secrets
// ---------------------------------------------------------------------------

resource anthropicApiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(anthropicApiKey)) {
  parent: keyVault
  name: 'anthropic-api-key'
  properties: {
    value: anthropicApiKey
  }
}

resource anthropicFoundryApiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(anthropicFoundryApiKey)) {
  parent: keyVault
  name: 'anthropic-foundry-api-key'
  properties: {
    value: anthropicFoundryApiKey
  }
}

resource entraClientSecretSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(entraClientSecret)) {
  parent: keyVault
  name: 'entra-client-secret'
  properties: {
    value: entraClientSecret
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Resource ID of the Key Vault')
output keyVaultId string = keyVault.id

@description('Name of the Key Vault')
output keyVaultName string = keyVault.name

@description('URI of the Key Vault')
output keyVaultUri string = keyVault.properties.vaultUri
