#Requires -Version 7.0
<#
.SYNOPSIS
    Build and deploy API container to Azure Container Apps (Windows equivalent of 'just api-deploy').

.PARAMETER AcrName
    Azure Container Registry name. Default: acrsurfdev

.PARAMETER ResourceGroup
    Resource group containing ACR and Container App. Default: rg-surf-dev

.PARAMETER ContainerAppName
    Container App name to update. Default: ca-api-surf-dev
#>
[CmdletBinding()]
param(
    [string]$AcrName = 'acrsurfdev',
    [string]$ResourceGroup = 'rg-surf-dev',
    [string]$ContainerAppName = 'ca-api-surf-dev'
)

$ErrorActionPreference = 'Stop'

$Tag = git rev-parse --short HEAD
$AcrServer = az acr show --name $AcrName --resource-group $ResourceGroup --query loginServer -o tsv
$Image = "${AcrServer}/surf-api:${Tag}"

Write-Host 'Logging in to ACR...'
az acr login --name $AcrName

Write-Host "Building ${Image}..."
docker build -t $Image -f api/Dockerfile api/

Write-Host "Pushing ${Image}..."
docker push $Image

Write-Host 'Updating Container App...'
az containerapp update --name $ContainerAppName --resource-group $ResourceGroup --image $Image --output none

Write-Host "API deployed: ${Image}"
