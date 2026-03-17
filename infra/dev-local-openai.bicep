// ---------------------------------------------------------------------------
// Dev-Local OpenAI Infrastructure: dev-local-openai.bicep
// Project: Surf — Multi-Agent Orchestration Platform
// Description: Azure OpenAI resource for embeddings only (chat uses Anthropic
//              Claude directly). Deployed by 'just setup-dev' into
//              rg-surf-dev-ai alongside the main rg-surf-dev resource group.
// ---------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Azure region for OpenAI')
param location string = 'eastus2'

@description('Object ID of the signed-in user (for RBAC)')
param userObjectId string

@description('Project name used as a naming prefix')
param projectName string = 'surf'

var uniqueSuffix = uniqueString(resourceGroup().id, projectName)
var tags = {
  project: projectName
  environment: 'dev-local'
}

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'oai-${projectName}-dev-${uniqueSuffix}'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: 'oai-${projectName}-dev-${uniqueSuffix}'
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: 'text-embedding-3-large'
  sku: {
    name: 'Standard'
    capacity: 50
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-large'
      version: '1'
    }
  }
}

resource openAiRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.id, userObjectId, 'cognitive-services-openai-user')
  scope: openAi
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: userObjectId
    principalType: 'User'
  }
}

@description('Azure OpenAI endpoint')
output openAiEndpoint string = openAi.properties.endpoint
