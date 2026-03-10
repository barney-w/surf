#Requires -Version 7.0
<#
.SYNOPSIS
    Build and deploy web frontend to Azure Static Web Apps (Windows equivalent of 'just web-deploy').

.PARAMETER ContainerAppName
    Container App name to look up the API FQDN. Default: ca-api-surf-dev

.PARAMETER ResourceGroup
    Resource group containing the Container App. Default: rg-surf-dev

.PARAMETER SwaName
    Static Web App name. Default: swa-surf-dev

.PARAMETER SwaResourceGroup
    Resource group containing the Static Web App. Default: rg-surf-dev-ai
#>
[CmdletBinding()]
param(
    [string]$ContainerAppName = 'ca-api-surf-dev',
    [string]$ResourceGroup = 'rg-surf-dev',
    [string]$SwaName = 'swa-surf-dev',
    [string]$SwaResourceGroup = 'rg-surf-dev-ai'
)

$ErrorActionPreference = 'Stop'

$ApiFqdn = az containerapp show --name $ContainerAppName --resource-group $ResourceGroup --query 'properties.configuration.ingress.fqdn' -o tsv
$ApiUrl = "https://${ApiFqdn}/api/v1"
Write-Host "Building with API URL: $ApiUrl"

$env:VITE_SURF_API_URL = $ApiUrl
Push-Location web
try {
    npm run build
    $SwaToken = az staticwebapp secrets list --name $SwaName --resource-group $SwaResourceGroup --query 'properties.apiKey' -o tsv
    npx swa deploy dist --deployment-token $SwaToken --app-name $SwaName --env production
}
finally {
    Pop-Location
}
