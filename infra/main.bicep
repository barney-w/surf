// ---------------------------------------------------------------------------
// Orchestrator: main.bicep
// Project: Surf — Multi-Agent Orchestration Platform
// Description: Deploys all infrastructure modules for the Surf platform.
// ---------------------------------------------------------------------------

targetScope = 'resourceGroup'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Environment name')
@allowed(['dev', 'staging', 'prod'])
param environmentName string

@description('Azure region for all resources')
param location string = 'australiaeast'

@description('Project name used as a naming prefix')
param projectName string = 'surf'

// Module-specific SKU / capacity parameters
@description('Azure AI Search SKU')
param aiSearchSku string = 'basic'

@description('Azure AI Search replica count')
param aiSearchReplicaCount int = 1

@description('Azure AI Search partition count')
param aiSearchPartitionCount int = 1

@description('Azure AI Search SharePoint index name (empty to disable)')
param aiSearchSharepointIndex string = ''

@description('Override Azure AI Search endpoint (use external service instead of Bicep-managed one)')
param aiSearchEndpointOverride string = ''

@description('Storage account SKU')
param storageSku string = 'Standard_LRS'

@description('Key Vault SKU')
param keyVaultSku string = 'standard'

@description('Key Vault enable purge protection')
param keyVaultEnablePurgeProtection bool = false

@description('Allow public network access to Key Vault')
@allowed(['Enabled', 'Disabled'])
param keyVaultPublicNetworkAccess string = 'Enabled'

@description('Log Analytics retention in days')
param logAnalyticsRetentionDays int = 30

@description('Allow public network access to Log Analytics (Enabled only for dev)')
@allowed(['Enabled', 'Disabled'])
param logAnalyticsPublicNetworkAccess string = 'Disabled'

@description('ACR SKU')
param acrSku string = 'Basic'

@description('Allow public network access to ACR (needed for GitHub-hosted runners in dev)')
@allowed(['Enabled', 'Disabled'])
param acrPublicNetworkAccess string = 'Disabled'

@description('Container Apps CPU cores')
param containerAppsCpu string = '0.5'

@description('Container Apps memory')
param containerAppsMemory string = '1Gi'

@description('API minimum replicas')
param apiMinReplicas int = 0

@description('API maximum replicas')
param apiMaxReplicas int = 3

@description('Ingestion minimum replicas')
param ingestionMinReplicas int = 0

@description('Ingestion maximum replicas')
param ingestionMaxReplicas int = 1

@description('Embedding model capacity')
param embeddingCapacity int = 10

@secure()
@description('Anthropic API key for direct API access (stored in Key Vault, passed to surf-api container)')
param anthropicApiKey string = ''

@description('Anthropic model ID for coordinator (routing) agent')
param anthropicModelId string = 'claude-haiku-4-5-20251001'

@description('Anthropic model ID for domain (specialist) agents — defaults to Sonnet for quality')
param anthropicDomainModelId string = 'claude-sonnet-4-6'

@description('Anthropic Foundry base URL (empty to use direct Anthropic API)')
param anthropicFoundryBaseUrl string = ''

@secure()
@description('Anthropic Foundry API key (stored in Key Vault, used when anthropicFoundryBaseUrl is set)')
param anthropicFoundryApiKey string = ''

@secure()
@description('Entra ID client secret for OBO flow (stored in Key Vault)')
param entraClientSecret string = ''

@description('Set true after initial deployment once anthropic-api-key exists in Key Vault')
param anthropicApiKeyInKv bool = true

@description('Set true after initial deployment once entra-client-secret exists in Key Vault')
param entraClientSecretInKv bool = true

@description('Entra ID tenant ID')
param entraTenantId string = ''

@description('Entra ID client ID (app registration)')
param entraClientId string = ''

@secure()
@description('HMAC secret for guest access tokens (stored in Key Vault)')
param guestTokenSecret string = ''

@description('Set true after initial deployment once guest-token-secret exists in Key Vault')
param guestTokenSecretInKv bool = false

@description('Enable authentication (true for staging/prod)')
param authEnabled bool = false

@description('Whether the Container Apps Environment is internal (VNet-only)')
param containerAppsInternal bool = false

@description('Organisation name displayed in agent prompts (empty = generic)')
param organisationName string = ''

@description('CORS allowed origins for surf-api (JSON array string)')
param apiCorsOrigins string = '["http://localhost:3000"]'

@description('Web minimum replicas')
param webMinReplicas int = 1

@description('Web maximum replicas')
param webMaxReplicas int = 1

// ---------------------------------------------------------------------------
// GitLab CI/CD — Workload Identity Federation (OIDC)
// ---------------------------------------------------------------------------
// Creates a dedicated CI managed identity with a federated credential so
// GitLab pipelines authenticate via short-lived OIDC tokens instead of
// stored service-principal secrets. Set both params to enable.
//
// Bootstrap (one-time):
//   1. Deploy this template manually with gitlabOidcIssuer + gitlabProjectPath
//   2. Note the output ciIdentityClientId
//   3. Set AZURE_CI_CLIENT_ID in GitLab CI/CD variables (masked)
//   4. GitLab pipelines now authenticate via WIF — no secrets stored
// ---------------------------------------------------------------------------

@description('GitLab OIDC issuer URL for Workload Identity Federation (e.g. https://gitlab.example.com). Empty to disable.')
param gitlabOidcIssuer string = ''

@description('GitLab project path for federated credential subject claim (e.g. group/surf)')
param gitlabProjectPath string = ''

@description('Container image tag for surf-api (set by CI/CD)')
param apiImageTag string = ''

@description('Container image tag for surf-ingestion (set by CI/CD)')
param ingestionImageTag string = ''

@description('Container image tag for surf-web (set by CI/CD)')
param webImageTag string = ''

@description('Custom domain hostname for the web app (e.g. chatwith.surf). Leave empty to skip.')
param webCustomDomain string = ''

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------

var baseName = '${projectName}-${environmentName}'
var tags = {
  project: projectName
  environment: environmentName
}

// Unique suffix for globally-unique names
var uniqueSuffix = uniqueString(resourceGroup().id, projectName, environmentName)

// GitLab CI/CD Workload Identity Federation
var enableGitlabOidc = !empty(gitlabOidcIssuer) && !empty(gitlabProjectPath)

// Container image variables — use a public placeholder on first bootstrap
var bootstrapImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var apiImage = !empty(apiImageTag) ? '${acr.outputs.loginServer}/surf-api:${apiImageTag}' : bootstrapImage
var ingestionImage = !empty(ingestionImageTag) ? '${acr.outputs.loginServer}/surf-ingestion:${ingestionImageTag}' : bootstrapImage
var webImage = !empty(webImageTag) ? '${acr.outputs.loginServer}/surf-web:${webImageTag}' : bootstrapImage
var keyVaultName = keyVault.outputs.name

// Allow overriding the search endpoint (e.g. to use a pre-existing service with indexed data)
var resolvedSearchEndpoint = !empty(aiSearchEndpointOverride) ? aiSearchEndpointOverride : aiSearch.outputs.endpoint

// Pre-computed managed identity resource ID (identity blocks require values calculable at deployment start)
var managedIdentityResourceId = resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', 'id-${baseName}')

// ---------------------------------------------------------------------------
// Module: Log Analytics
// ---------------------------------------------------------------------------

module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.9.1' = {
  name: 'deploy-log-analytics'
  params: {
    name: 'log-${baseName}'
    location: location
    tags: tags
    skuName: 'PerGB2018'
    dataRetention: logAnalyticsRetentionDays
    publicNetworkAccessForIngestion: logAnalyticsPublicNetworkAccess
    publicNetworkAccessForQuery: logAnalyticsPublicNetworkAccess
  }
}

// ---------------------------------------------------------------------------
// Module: Managed Identity
// ---------------------------------------------------------------------------

module managedIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.5.0' = {
  name: 'deploy-managed-identity'
  params: {
    name: 'id-${baseName}'
    location: location
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// Module: CI/CD Managed Identity (GitLab Workload Identity Federation)
// ---------------------------------------------------------------------------
// Separate identity from the workload identity (id-${baseName}) so CI/CD
// permissions (AcrPush, Contributor) are isolated from runtime permissions
// (AcrPull, Key Vault Secrets User, etc.).

module ciManagedIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.5.0' = if (enableGitlabOidc) {
  name: 'deploy-ci-managed-identity'
  params: {
    name: 'id-ci-${baseName}'
    location: location
    tags: tags
    federatedIdentityCredentials: [
      {
        name: 'gitlab-ci-main'
        issuer: gitlabOidcIssuer
        subject: 'project_path:${gitlabProjectPath}:ref_type:branch:ref:main'
        audiences: ['api://AzureADTokenExchange']
      }
      {
        name: 'gitlab-ci-main-env-dev'
        issuer: gitlabOidcIssuer
        subject: 'project_path:${gitlabProjectPath}:ref_type:branch:ref:main:environment:dev'
        audiences: ['api://AzureADTokenExchange']
      }
    ]
  }
}

// Role: CI identity → Contributor on resource group (required for az deployment group create)
resource ciContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableGitlabOidc) {
  name: guid(resourceGroup().id, 'ci-contributor', baseName)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c') // Contributor
    #disable-next-line BCP318 // Safe: guarded by same enableGitlabOidc condition
    principalId: ciManagedIdentity.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role: CI identity → User Access Administrator on resource group (required for role assignments in Bicep).
// ABAC condition restricts which roles CI can assign — prevents privilege escalation.
resource ciUserAccessAdminRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableGitlabOidc) {
  name: guid(resourceGroup().id, 'ci-user-access-admin', baseName)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '18d7d88d-d35e-4fb5-a5c3-7773c20a72d9') // User Access Administrator
    #disable-next-line BCP318 // Safe: guarded by same enableGitlabOidc condition
    principalId: ciManagedIdentity.outputs.principalId
    principalType: 'ServicePrincipal'
    conditionVersion: '2.0'
    // Allow CI to assign only the specific roles that Bicep resources require:
    // Cognitive Services OpenAI User, Search Index Data Contributor,
    // Key Vault Secrets User, Storage Blob Data Contributor, AcrPull
    condition: '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/write\'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAllValues:GuidEquals {5e0bd9bd-7b93-4f28-af87-19fc36ad61bd, 8ebe5a00-799e-43f5-93ac-243d3dce84a7, 4633458b-17de-408a-b874-0445c86b69e6, ba92f5b4-2d11-453d-a403-e96b0029c9fe, 7f951dda-4ed3-4680-a7ca-43fe172d538d}))'
  }
}

// Role: CI identity → AcrPush on container registry (for docker push from CI)
resource ciAcrPushRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableGitlabOidc) {
  name: guid(resourceGroup().id, 'ci-acr-push', baseName)
  scope: acrResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8311e382-0749-4cb8-b61a-304f252e45ec') // AcrPush
    #disable-next-line BCP318 // Safe: guarded by same enableGitlabOidc condition
    principalId: ciManagedIdentity.outputs.principalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [acr]
}

// Reference to ACR for scoping the AcrPush role assignment
resource acrResource 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: replace('acr${baseName}', '-', '')
}

// ---------------------------------------------------------------------------
// Module: Azure OpenAI (embeddings only — chat uses Anthropic Claude directly)
// ---------------------------------------------------------------------------

module openAi 'br/public:avm/res/cognitive-services/account:0.10.1' = {
  name: 'deploy-openai'
  params: {
    name: 'oai-${baseName}'
    location: location
    tags: tags
    kind: 'OpenAI'
    sku: 'S0'
    customSubDomainName: 'oai-${baseName}'
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
    }
    managedIdentities: {
      systemAssigned: true
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
          capacity: embeddingCapacity
        }
      }
    ]
    roleAssignments: [
      {
        principalId: managedIdentity.outputs.principalId
        roleDefinitionIdOrName: 'Cognitive Services OpenAI User'
        principalType: 'ServicePrincipal'
      }
    ]
    privateEndpoints: [
      {
        subnetResourceId: vnet.outputs.subnetResourceIds[1]
        privateDnsZoneGroup: {
          privateDnsZoneGroupConfigs: [
            {
              privateDnsZoneResourceId: dnsZoneOpenAi.outputs.resourceId
            }
          ]
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Module: Azure AI Search
// ---------------------------------------------------------------------------

module aiSearch 'br/public:avm/res/search/search-service:0.12.0' = {
  name: 'deploy-ai-search'
  params: {
    name: 'search-${baseName}'
    location: location
    tags: tags
    sku: aiSearchSku
    replicaCount: aiSearchReplicaCount
    partitionCount: aiSearchPartitionCount
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
    managedIdentities: {
      systemAssigned: true
    }
    roleAssignments: [
      {
        principalId: managedIdentity.outputs.principalId
        roleDefinitionIdOrName: 'Search Index Data Contributor'
        principalType: 'ServicePrincipal'
      }
    ]
    privateEndpoints: [
      {
        subnetResourceId: vnet.outputs.subnetResourceIds[1]
        privateDnsZoneGroup: {
          privateDnsZoneGroupConfigs: [
            {
              privateDnsZoneResourceId: dnsZoneSearch.outputs.resourceId
            }
          ]
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Module: Key Vault
// ---------------------------------------------------------------------------

module keyVault 'br/public:avm/res/key-vault/vault:0.13.3' = {
  name: 'deploy-key-vault'
  params: {
    name: 'kv-${baseName}-${take(uniqueSuffix, 10)}'
    location: location
    tags: tags
    sku: keyVaultSku
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: keyVaultEnablePurgeProtection
    publicNetworkAccess: keyVaultPublicNetworkAccess
    networkAcls: {
      defaultAction: keyVaultPublicNetworkAccess == 'Enabled' ? 'Allow' : 'Deny'
      bypass: 'AzureServices'
    }
    secrets: concat(
      !empty(anthropicApiKey) ? [{ name: 'anthropic-api-key', value: anthropicApiKey }] : [],
      !empty(anthropicFoundryApiKey) ? [{ name: 'anthropic-foundry-api-key', value: anthropicFoundryApiKey }] : [],
      !empty(entraClientSecret) ? [{ name: 'entra-client-secret', value: entraClientSecret }] : [],
      !empty(guestTokenSecret) ? [{ name: 'guest-token-secret', value: guestTokenSecret }] : []
    )
    roleAssignments: [
      {
        principalId: managedIdentity.outputs.principalId
        roleDefinitionIdOrName: 'Key Vault Secrets User'
        principalType: 'ServicePrincipal'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Module: Network Security Groups
// ---------------------------------------------------------------------------

module nsgContainerApps 'br/public:avm/res/network/network-security-group:0.5.0' = {
  name: 'deploy-nsg-container-apps'
  params: {
    name: 'nsg-vnet-${baseName}-container-apps'
    location: location
    tags: tags
    securityRules: [
      {
        name: 'AllowHTTPSInbound'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      {
        name: 'AllowContainerAppsToPrivateEndpoints'
        properties: {
          priority: 110
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '10.0.0.0/23'
          sourcePortRange: '*'
          destinationAddressPrefix: '10.0.2.0/24'
          destinationPortRange: '443'
        }
      }
    ]
  }
}

module nsgPrivateEndpoints 'br/public:avm/res/network/network-security-group:0.5.0' = {
  name: 'deploy-nsg-private-endpoints'
  params: {
    name: 'nsg-vnet-${baseName}-private-endpoints'
    location: location
    tags: tags
    securityRules: [
      {
        name: 'AllowInboundFromContainerApps'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '10.0.0.0/23'
          sourcePortRange: '*'
          destinationAddressPrefix: '10.0.2.0/24'
          destinationPortRange: '443'
        }
      }
      {
        name: 'DenyAllOtherInbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Module: Virtual Network
// ---------------------------------------------------------------------------

module vnet 'br/public:avm/res/network/virtual-network:0.7.2' = {
  name: 'deploy-vnet'
  params: {
    name: 'vnet-${baseName}'
    location: location
    tags: tags
    addressPrefixes: [
      '10.0.0.0/16'
    ]
    subnets: [
      {
        name: 'snet-container-apps'
        addressPrefix: '10.0.0.0/23'
        networkSecurityGroupResourceId: nsgContainerApps.outputs.resourceId
        delegation: 'Microsoft.App/environments'
      }
      {
        name: 'snet-private-endpoints'
        addressPrefix: '10.0.2.0/24'
        networkSecurityGroupResourceId: nsgPrivateEndpoints.outputs.resourceId
        privateEndpointNetworkPolicies: 'Disabled'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Module: Private DNS Zones
// ---------------------------------------------------------------------------

module dnsZoneSearch 'br/public:avm/res/network/private-dns-zone:0.7.0' = {
  name: 'deploy-dns-zone-search'
  params: {
    name: 'privatelink.search.windows.net'
    tags: tags
    virtualNetworkLinks: [
      {
        virtualNetworkResourceId: vnet.outputs.resourceId
        registrationEnabled: false
      }
    ]
  }
}

module dnsZoneStorage 'br/public:avm/res/network/private-dns-zone:0.7.0' = {
  name: 'deploy-dns-zone-storage'
  params: {
    name: 'privatelink.blob.${environment().suffixes.storage}'
    tags: tags
    virtualNetworkLinks: [
      {
        virtualNetworkResourceId: vnet.outputs.resourceId
        registrationEnabled: false
      }
    ]
  }
}

module dnsZoneOpenAi 'br/public:avm/res/network/private-dns-zone:0.7.0' = {
  name: 'deploy-dns-zone-openai'
  params: {
    name: 'privatelink.openai.azure.com'
    tags: tags
    virtualNetworkLinks: [
      {
        virtualNetworkResourceId: vnet.outputs.resourceId
        registrationEnabled: false
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Module: Storage Account
// ---------------------------------------------------------------------------

module storage 'br/public:avm/res/storage/storage-account:0.14.3' = {
  name: 'deploy-storage'
  params: {
    name: 'st${projectName}${environmentName}${uniqueSuffix}'
    location: location
    tags: tags
    skuName: storageSku
    kind: 'StorageV2'
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    requireInfrastructureEncryption: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
    blobServices: {
      deleteRetentionPolicyEnabled: true
      deleteRetentionPolicyDays: 7
      containers: [
        { name: 'documents', publicAccess: 'None' }
        { name: 'ingested', publicAccess: 'None' }
      ]
    }
    roleAssignments: [
      {
        principalId: managedIdentity.outputs.principalId
        roleDefinitionIdOrName: 'Storage Blob Data Contributor'
        principalType: 'ServicePrincipal'
      }
    ]
    privateEndpoints: [
      {
        service: 'blob'
        subnetResourceId: vnet.outputs.subnetResourceIds[1]
        privateDnsZoneGroup: {
          privateDnsZoneGroupConfigs: [
            {
              privateDnsZoneResourceId: dnsZoneStorage.outputs.resourceId
            }
          ]
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Module: Azure Container Registry
// ---------------------------------------------------------------------------

module acr 'br/public:avm/res/container-registry/registry:0.11.0' = {
  name: 'deploy-acr'
  params: {
    name: replace('acr${baseName}', '-', '')
    location: location
    tags: tags
    acrSku: acrSku
    acrAdminUserEnabled: false
    publicNetworkAccess: acrPublicNetworkAccess
    roleAssignments: [
      {
        principalId: managedIdentity.outputs.principalId
        roleDefinitionIdOrName: 'AcrPull'
        principalType: 'ServicePrincipal'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Resource: Container Apps Environment (native Bicep — requires listKeys for Log Analytics)
// ---------------------------------------------------------------------------

resource logAnalyticsRef 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: 'log-${baseName}'
}

resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${baseName}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsRef.properties.customerId
        sharedKey: logAnalyticsRef.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: vnet.outputs.subnetResourceIds[0]
      internal: containerAppsInternal
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Resource: Container App — surf-api
// ---------------------------------------------------------------------------

resource surfApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-api-${baseName}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8000
        transport: 'auto'
        allowInsecure: true  // Accept HTTP from same-environment apps
      }
      secrets: concat(
        (!empty(keyVaultName) && anthropicApiKeyInKv) ? [
          {
            name: 'anthropic-api-key'
            keyVaultUrl: '${keyVault.outputs.uri}secrets/anthropic-api-key'
            identity: managedIdentityResourceId
          }
        ] : [],
        (!empty(keyVaultName) && !empty(anthropicFoundryApiKey)) ? [
          {
            name: 'anthropic-foundry-api-key'
            keyVaultUrl: '${keyVault.outputs.uri}secrets/anthropic-foundry-api-key'
            identity: managedIdentityResourceId
          }
        ] : [],
        (!empty(keyVaultName) && entraClientSecretInKv) ? [
          {
            name: 'entra-client-secret'
            keyVaultUrl: '${keyVault.outputs.uri}secrets/entra-client-secret'
            identity: managedIdentityResourceId
          }
        ] : [],
        (!empty(keyVaultName) && guestTokenSecretInKv) ? [
          {
            name: 'guest-token-secret'
            keyVaultUrl: '${keyVault.outputs.uri}secrets/guest-token-secret'
            identity: managedIdentityResourceId
          }
        ] : []
      )
      registries: [
        {
          server: acr.outputs.loginServer
          identity: managedIdentityResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'surf-api'
          image: apiImage
          resources: {
            cpu: json(containerAppsCpu)
            memory: containerAppsMemory
          }
          env: concat([
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAi.outputs.endpoint }
            { name: 'AZURE_SEARCH_ENDPOINT', value: resolvedSearchEndpoint }
            { name: 'AZURE_SEARCH_SHAREPOINT_INDEX', value: aiSearchSharepointIndex }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storage.outputs.primaryBlobEndpoint }
            { name: 'AZURE_KEYVAULT_URL', value: keyVault.outputs.uri }
            { name: 'AZURE_CLIENT_ID', value: managedIdentity.outputs.clientId }
            { name: 'ANTHROPIC_MODEL_ID', value: anthropicModelId }
            { name: 'ANTHROPIC_DOMAIN_MODEL_ID', value: anthropicDomainModelId }
            { name: 'API_CORS_ORIGINS', value: apiCorsOrigins }
            { name: 'AUTH_ENABLED', value: string(authEnabled) }
            { name: 'ENTRA_TENANT_ID', value: entraTenantId }
            { name: 'ENTRA_CLIENT_ID', value: entraClientId }
            { name: 'POSTGRES_ENABLED', value: 'False' }
          ], concat(
            !empty(organisationName) ? [{ name: 'ORGANISATION_NAME', value: organisationName }] : [],
            anthropicApiKeyInKv ? [{ name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }] : [],
            !empty(anthropicFoundryBaseUrl) ? [{ name: 'ANTHROPIC_FOUNDRY_BASE_URL', value: anthropicFoundryBaseUrl }] : [],
            !empty(anthropicFoundryApiKey) ? [{ name: 'ANTHROPIC_FOUNDRY_API_KEY', secretRef: 'anthropic-foundry-api-key' }] : [],
            entraClientSecretInKv ? [{ name: 'ENTRA_CLIENT_SECRET', secretRef: 'entra-client-secret' }] : [],
            guestTokenSecretInKv ? [{ name: 'GUEST_TOKEN_SECRET', secretRef: 'guest-token-secret' }] : []
          ))
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/v1/health'
                port: 8000
              }
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Startup'
              httpGet: {
                path: '/api/v1/health'
                port: 8000
              }
              periodSeconds: 10
              failureThreshold: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Resource: Container App — surf-ingestion (no ingress)
// ---------------------------------------------------------------------------

resource surfIngestion 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-ingestion-${baseName}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: acr.outputs.loginServer
          identity: managedIdentityResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'surf-ingestion'
          image: ingestionImage
          resources: {
            cpu: json(containerAppsCpu)
            memory: containerAppsMemory
          }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAi.outputs.endpoint }
            { name: 'AZURE_SEARCH_ENDPOINT', value: resolvedSearchEndpoint }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storage.outputs.primaryBlobEndpoint }
            { name: 'AZURE_KEYVAULT_URL', value: keyVault.outputs.uri }
            { name: 'AZURE_CLIENT_ID', value: managedIdentity.outputs.clientId }
          ]
        }
      ]
      scale: {
        minReplicas: ingestionMinReplicas
        maxReplicas: ingestionMaxReplicas
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Resource: Managed Certificate for custom domain (TXT validation)
// ---------------------------------------------------------------------------

resource webManagedCert 'Microsoft.App/managedEnvironments/managedCertificates@2024-03-01' = if (!empty(webCustomDomain)) {
  parent: containerAppsEnv
  name: 'mc-${containerAppsEnv.name}-${replace(webCustomDomain, '.', '-')}'
  location: location
  tags: tags
  properties: {
    subjectName: webCustomDomain
    domainControlValidation: 'TXT'
  }
}

// ---------------------------------------------------------------------------
// Resource: Container App — surf-web (nginx reverse proxy + SPA)
// ---------------------------------------------------------------------------

resource surfWeb 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-web-${baseName}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
        customDomains: !empty(webCustomDomain) ? [
          {
            name: webCustomDomain
            bindingType: 'SniEnabled'
            certificateId: webManagedCert.id
          }
        ] : []
      }
      registries: [
        {
          server: acr.outputs.loginServer
          identity: managedIdentityResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'surf-web'
          image: webImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'API_INTERNAL_FQDN', value: surfApi.properties.configuration.ingress.fqdn }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8080
              }
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Startup'
              httpGet: {
                path: '/healthz'
                port: 8080
              }
              periodSeconds: 10
              failureThreshold: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: webMinReplicas
        maxReplicas: webMaxReplicas
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
            }
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Module: Alert Rules
// ---------------------------------------------------------------------------

module restartAlert 'br/public:avm/res/insights/metric-alert:0.4.1' = {
  name: 'deploy-alert-restarts'
  params: {
    name: 'alert-container-restarts'
    severity: 2
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [
      surfApi.id
    ]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allof: [
        {
          name: 'RestartCount'
          metricName: 'RestartCount'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
  }
}

module fiveXxAlert 'br/public:avm/res/insights/metric-alert:0.4.1' = {
  name: 'deploy-alert-5xx'
  params: {
    name: 'alert-5xx-response-rate'
    alertDescription: 'Fires when the 5xx response rate exceeds 5% over a 5-minute window.'
    severity: 2
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [
      surfApi.id
    ]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allof: [
        {
          name: 'Requests5xx'
          metricName: 'Requests'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 5
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
          dimensions: [
            {
              name: 'statusCodeCategory'
              operator: 'Include'
              values: ['5xx']
            }
          ]
        }
      ]
    }
  }
}

module cpuAlert 'br/public:avm/res/insights/metric-alert:0.4.1' = {
  name: 'deploy-alert-cpu'
  params: {
    name: 'alert-cpu-high'
    alertDescription: 'Fires when container app CPU usage exceeds 80% for 5 minutes.'
    severity: 2
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [
      surfApi.id
    ]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allof: [
        {
          name: 'CpuUsage'
          metricName: 'UsageNanoCores'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 800000000
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Container Apps Environment name')
output containerAppsEnvName string = containerAppsEnv.name

@description('ACR login server')
output acrLoginServer string = acr.outputs.loginServer

@description('Azure OpenAI endpoint')
output openAiEndpoint string = openAi.outputs.endpoint

@description('Azure AI Search endpoint')
output aiSearchEndpoint string = aiSearch.outputs.endpoint

@description('Key Vault URI')
output keyVaultUri string = keyVault.outputs.uri

@description('Storage blob endpoint')
output storageBlobEndpoint string = storage.outputs.primaryBlobEndpoint

@description('VNet name')
output vnetName string = vnet.outputs.name

@description('FQDN of the surf-web container app')
output surfWebFqdn string = surfWeb.properties.configuration.ingress.fqdn

@description('CI managed identity client ID (set as AZURE_CI_CLIENT_ID in GitLab CI/CD variables)')
#disable-next-line BCP318 // Safe: ternary guards the conditional module access
output ciIdentityClientId string = enableGitlabOidc ? ciManagedIdentity.outputs.clientId : ''
