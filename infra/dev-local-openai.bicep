// WARNING: Dev-only template — do NOT use for staging or production deployments.
// Public network access is intentionally enabled for local developer convenience.
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

module openAi 'br/public:avm/res/cognitive-services/account:0.10.1' = {
  name: 'deploy-openai'
  params: {
    name: 'oai-${projectName}-dev-${uniqueSuffix}'
    location: location
    tags: tags
    kind: 'OpenAI'
    sku: 'S0'
    customSubDomainName: 'oai-${projectName}-dev-${uniqueSuffix}'
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
    deployments: [
      {
        name: 'text-embedding-3-large'
        model: {
          format: 'OpenAI'
          name: 'text-embedding-3-large'
          version: '1'
        }
        sku: {
          name: 'Standard'
          capacity: 50
        }
      }
    ]
    roleAssignments: [
      {
        principalId: userObjectId
        roleDefinitionIdOrName: 'Cognitive Services OpenAI User'
        principalType: 'User'
      }
    ]
  }
}

@description('Azure OpenAI endpoint')
output openAiEndpoint string = openAi.outputs.endpoint
