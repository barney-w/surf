#Requires -Version 7.0
<#
.SYNOPSIS
    Build and deploy web frontend to Azure Container Apps (Windows equivalent of 'just web-deploy').

.PARAMETER AcrName
    ACR name. Default: acrsurfdev

.PARAMETER ContainerAppName
    Container App name for the web frontend. Default: ca-web-surf-dev

.PARAMETER ResourceGroup
    Resource group containing the Container App. Default: rg-surf-dev
#>
[CmdletBinding()]
param(
    [string]$AcrName = 'acrsurfdev',
    [string]$ContainerAppName = 'ca-web-surf-dev',
    [string]$ResourceGroup = 'rg-surf-dev'
)

$ErrorActionPreference = 'Stop'

$Tag = (git rev-parse --short HEAD)
$AcrServer = (az acr show --name $AcrName --resource-group $ResourceGroup --query loginServer -o tsv)
$Image = "${AcrServer}/surf-web:${Tag}"

# Read Entra env vars from web/.env.local (Vite doesn't load .env.local for production builds)
if (Test-Path 'web/.env.local') {
    foreach ($line in Get-Content 'web/.env.local') {
        if ($line -match '^VITE_ENTRA_CLIENT_ID=(.+)') { $env:VITE_ENTRA_CLIENT_ID = $Matches[1] }
        if ($line -match '^VITE_ENTRA_TENANT_ID=(.+)') { $env:VITE_ENTRA_TENANT_ID = $Matches[1] }
    }
}
if (-not $env:VITE_ENTRA_TENANT_ID) {
    $env:VITE_ENTRA_TENANT_ID = (az account show --query tenantId -o tsv)
}
Write-Host "Entra Client ID: $($env:VITE_ENTRA_CLIENT_ID)"
Write-Host "Entra Tenant ID: $($env:VITE_ENTRA_TENANT_ID)"

Write-Host "Building web SPA..."
Push-Location web
try {
    npm run build
}
finally {
    Pop-Location
}

Write-Host "Logging in to ACR..."
az acr login --name $AcrName

Write-Host "Building ${Image}..."
docker build --platform linux/amd64 -t $Image -f web/Dockerfile web

Write-Host "Pushing ${Image}..."
docker push $Image

Write-Host "Updating Container App..."
az containerapp update --name $ContainerAppName --resource-group $ResourceGroup --image $Image --output none

Write-Host "Web deployed: ${Image}"
