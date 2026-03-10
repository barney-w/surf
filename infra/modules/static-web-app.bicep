// ---------------------------------------------------------------------------
// Module: Azure Static Web App
// Project: Surf — Multi-Agent Orchestration Platform
// ---------------------------------------------------------------------------

@description('Name for the Static Web App resource')
param staticWebAppName string

@description('Azure region (SWA has limited availability — use eastasia or centralus if needed)')
param location string

@description('Tags to apply to all resources')
param tags object

@description('SKU for the Static Web App')
@allowed(['Free', 'Standard'])
param skuName string = 'Free'

@description('Resource ID of the Container App to link as backend (empty to skip — requires Standard SKU)')
param containerAppResourceId string = ''

@description('Region of the linked backend (must match Container App region)')
param backendRegion string = location

// ---------------------------------------------------------------------------
// Static Web App
// ---------------------------------------------------------------------------

resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: staticWebAppName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuName
  }
  properties: {
    stagingEnvironmentPolicy: 'Enabled'
    allowConfigFileUpdates: true
    buildProperties: {
      skipGithubActionWorkflowGeneration: true
    }
  }
}

// ---------------------------------------------------------------------------
// Linked Backend — proxies /api/* to the Container App (Standard SKU only)
// ---------------------------------------------------------------------------

resource linkedBackend 'Microsoft.Web/staticSites/linkedBackends@2023-12-01' = if (skuName == 'Standard' && !empty(containerAppResourceId)) {
  parent: staticWebApp
  name: 'api-backend'
  properties: {
    backendResourceId: containerAppResourceId
    region: backendRegion
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Name of the Static Web App')
output staticWebAppName string = staticWebApp.name

@description('Default hostname of the Static Web App')
output defaultHostname string = staticWebApp.properties.defaultHostname

@description('Deployment token for GitHub Actions')
output deploymentToken string = staticWebApp.listSecrets().properties.apiKey
