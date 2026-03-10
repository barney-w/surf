#Requires -Version 7.0
<#
.SYNOPSIS
    Delete dev resource groups (Windows equivalent of 'just teardown-dev').

.PARAMETER ResourceGroup
    Primary resource group name. Default: rg-surf-dev

.PARAMETER ResourceGroupAI
    AI resource group name. Default: rg-surf-dev-ai
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'rg-surf-dev',
    [string]$ResourceGroupAI = 'rg-surf-dev-ai'
)

$ErrorActionPreference = 'Stop'

$SubName = az account show --query name -o tsv
Write-Host "WARNING: This will DELETE $ResourceGroup and $ResourceGroupAI in subscription: $SubName"
$Confirm = Read-Host "Type 'yes' to confirm"
if ($Confirm -ne 'yes') {
    Write-Host 'Aborted.'
    exit 1
}

az group delete --name $ResourceGroup   --yes --no-wait
az group delete --name $ResourceGroupAI --yes --no-wait
Write-Host 'Resource group deletion initiated (runs in background).'
