// ---------------------------------------------------------------------------
// Module: Azure OpenAI
// Project: Surf — Multi-Agent Orchestration Platform
// ---------------------------------------------------------------------------

@description('Name of the Azure OpenAI resource')
param openAiName string

@description('Azure region for deployment')
param location string

@description('Tags to apply to all resources')
param tags object

@description('SKU for Azure OpenAI')
param skuName string = 'S0'

@description('Whether to deploy the GPT-5.2 chat model')
param deployGpt52 bool = true

@description('GPT-5.2 model capacity (in thousands of tokens per minute)')
param gpt52Capacity int = 10

@description('Whether to deploy a text-embedding model')
param deployEmbedding bool = true

@description('Embedding model capacity')
param embeddingCapacity int = 10

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: openAiName
  location: location
  tags: tags
  kind: 'OpenAI'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: openAiName
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
    }
  }
  sku: {
    name: skuName
  }
}

resource gpt52Deployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = if (deployGpt52) {
  parent: openAi
  name: 'gpt-5.2-chat'
  sku: {
    name: 'GlobalStandard'
    capacity: gpt52Capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.2-chat'
      version: '2026-02-10'
    }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = if (deployEmbedding) {
  parent: openAi
  name: 'text-embedding-3-large'
  sku: {
    name: 'Standard'
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-large'
      version: '1'
    }
  }
  dependsOn: [
    gpt52Deployment
  ]
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Resource ID of the Azure OpenAI account')
output openAiId string = openAi.id

@description('Name of the Azure OpenAI account')
output openAiName string = openAi.name

@description('Endpoint URL of the Azure OpenAI account')
output openAiEndpoint string = openAi.properties.endpoint

@description('Principal ID of the managed identity')
output openAiPrincipalId string = openAi.identity.principalId
