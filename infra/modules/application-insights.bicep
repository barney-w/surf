// ---------------------------------------------------------------------------
// Module: Application Insights
// Project: Surf — Multi-Agent Orchestration Platform
// Description: Workspace-based Application Insights for telemetry collection.
// ---------------------------------------------------------------------------

@description('Application Insights resource name')
param name string

@description('Azure region')
param location string

@description('Resource ID of the Log Analytics workspace')
param workspaceId string

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: name
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspaceId
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    RetentionInDays: 90
  }
}

@description('Application Insights connection string')
output connectionString string = appInsights.properties.ConnectionString

@description('Application Insights resource ID')
output resourceId string = appInsights.id
