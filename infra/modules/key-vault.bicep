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
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
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
