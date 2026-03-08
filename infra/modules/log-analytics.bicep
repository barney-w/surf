// ---------------------------------------------------------------------------
// Module: Log Analytics Workspace
// Project: Surf — Multi-Agent Orchestration Platform
// ---------------------------------------------------------------------------

@description('Name of the Log Analytics workspace')
param workspaceName string

@description('Azure region for deployment')
param location string

@description('SKU for Log Analytics workspace')
@allowed(['PerGB2018', 'Free', 'Standalone', 'PerNode'])
param skuName string = 'PerGB2018'

@description('Data retention in days')
@minValue(30)
@maxValue(730)
param retentionInDays int = 30

@description('Tags to apply to all resources')
param tags object

@description('Allow public network access for ingestion (Disabled for staging/prod)')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccessForIngestion string = 'Disabled'

@description('Allow public network access for queries (Disabled for staging/prod)')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccessForQuery string = 'Disabled'

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: skuName
    }
    retentionInDays: retentionInDays
    publicNetworkAccessForIngestion: publicNetworkAccessForIngestion
    publicNetworkAccessForQuery: publicNetworkAccessForQuery
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Resource ID of the Log Analytics workspace')
output workspaceId string = logAnalytics.id

@description('Customer ID (workspace ID) for Log Analytics')
output customerId string = logAnalytics.properties.customerId

@description('Name of the Log Analytics workspace')
output workspaceName string = logAnalytics.name

