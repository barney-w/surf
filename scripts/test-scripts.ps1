#Requires -Version 7.0
<#
.SYNOPSIS
    Validates the PowerShell deployment scripts without executing Azure commands.
    Run from the repo root: pwsh scripts/test-scripts.ps1
#>

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Pass = 0
$Fail = 0

function Assert($Description, [scriptblock]$Test) {
    try {
        $result = & $Test
        if ($result) {
            Write-Host "  PASS: $Description" -ForegroundColor Green
            $script:Pass++
        } else {
            Write-Host "  FAIL: $Description" -ForegroundColor Red
            $script:Fail++
        }
    } catch {
        Write-Host "  FAIL: $Description -- $_" -ForegroundColor Red
        $script:Fail++
    }
}

# ── 1. File existence ──────────────────────────────────────────────────
Write-Host "`n=== File Existence ===" -ForegroundColor Cyan

$ExpectedFiles = @(
    'scripts/setup-dev.ps1',
    'scripts/teardown-dev.ps1',
    'scripts/api-deploy.ps1',
    'scripts/web-deploy.ps1',
    'justfile.win',
    'docs/local-deployment.md'
)

foreach ($f in $ExpectedFiles) {
    Assert "$f exists" { Test-Path (Join-Path $RepoRoot $f) }
}

# ── 2. Syntax validation ──────────────────────────────────────────────
Write-Host "`n=== Syntax Validation ===" -ForegroundColor Cyan

$Scripts = @(
    'scripts/setup-dev.ps1',
    'scripts/teardown-dev.ps1',
    'scripts/api-deploy.ps1',
    'scripts/web-deploy.ps1'
)

foreach ($s in $Scripts) {
    $path = Join-Path $RepoRoot $s
    Assert "$s has no parse errors" {
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors) | Out-Null
        $errors.Count -eq 0
    }
}

# ── 3. Parameter validation ───────────────────────────────────────────
Write-Host "`n=== Parameter Definitions ===" -ForegroundColor Cyan

function Get-ScriptParams($RelPath) {
    $path = Join-Path $RepoRoot $RelPath
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$null)
    $paramBlock = $ast.ParamBlock
    if (-not $paramBlock) { return @() }
    return $paramBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath }
}

$SetupParams = Get-ScriptParams 'scripts/setup-dev.ps1'
Assert 'setup-dev.ps1 has ResourceGroup param'   { $SetupParams -contains 'ResourceGroup' }
Assert 'setup-dev.ps1 has ResourceGroupAI param'  { $SetupParams -contains 'ResourceGroupAI' }
Assert 'setup-dev.ps1 has Location param'          { $SetupParams -contains 'Location' }
Assert 'setup-dev.ps1 has LocationAI param'        { $SetupParams -contains 'LocationAI' }

$TeardownParams = Get-ScriptParams 'scripts/teardown-dev.ps1'
Assert 'teardown-dev.ps1 has ResourceGroup param'    { $TeardownParams -contains 'ResourceGroup' }
Assert 'teardown-dev.ps1 has ResourceGroupAI param'   { $TeardownParams -contains 'ResourceGroupAI' }

$ApiDeployParams = Get-ScriptParams 'scripts/api-deploy.ps1'
Assert 'api-deploy.ps1 has AcrName param'          { $ApiDeployParams -contains 'AcrName' }
Assert 'api-deploy.ps1 has ResourceGroup param'     { $ApiDeployParams -contains 'ResourceGroup' }
Assert 'api-deploy.ps1 has ContainerAppName param'  { $ApiDeployParams -contains 'ContainerAppName' }

$WebDeployParams = Get-ScriptParams 'scripts/web-deploy.ps1'
Assert 'web-deploy.ps1 has ContainerAppName param'  { $WebDeployParams -contains 'ContainerAppName' }
Assert 'web-deploy.ps1 has SwaName param'            { $WebDeployParams -contains 'SwaName' }
Assert 'web-deploy.ps1 has SwaResourceGroup param'   { $WebDeployParams -contains 'SwaResourceGroup' }

# ── 4. Default values match original justfile ─────────────────────────
Write-Host "`n=== Default Values ===" -ForegroundColor Cyan

function Get-DefaultValue($RelPath, $ParamName) {
    $path = Join-Path $RepoRoot $RelPath
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$null)
    $param = $ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq $ParamName }
    if ($param -and $param.DefaultValue) {
        return $param.DefaultValue.Value
    }
    return $null
}

Assert 'setup-dev ResourceGroup defaults to rg-surf-dev' {
    (Get-DefaultValue 'scripts/setup-dev.ps1' 'ResourceGroup') -eq 'rg-surf-dev'
}
Assert 'setup-dev ResourceGroupAI defaults to rg-surf-dev-ai' {
    (Get-DefaultValue 'scripts/setup-dev.ps1' 'ResourceGroupAI') -eq 'rg-surf-dev-ai'
}
Assert 'setup-dev Location defaults to australiaeast' {
    (Get-DefaultValue 'scripts/setup-dev.ps1' 'Location') -eq 'australiaeast'
}
Assert 'setup-dev LocationAI defaults to eastus2' {
    (Get-DefaultValue 'scripts/setup-dev.ps1' 'LocationAI') -eq 'eastus2'
}
Assert 'api-deploy AcrName defaults to acrsurfdev' {
    (Get-DefaultValue 'scripts/api-deploy.ps1' 'AcrName') -eq 'acrsurfdev'
}
Assert 'api-deploy ContainerAppName defaults to ca-api-surf-dev' {
    (Get-DefaultValue 'scripts/api-deploy.ps1' 'ContainerAppName') -eq 'ca-api-surf-dev'
}
Assert 'teardown-dev ResourceGroup defaults to rg-surf-dev' {
    (Get-DefaultValue 'scripts/teardown-dev.ps1' 'ResourceGroup') -eq 'rg-surf-dev'
}

# ── 5. Script content checks ─────────────────────────────────────────
Write-Host "`n=== Content Checks ===" -ForegroundColor Cyan

function Get-ScriptContent($RelPath) {
    Get-Content (Join-Path $RepoRoot $RelPath) -Raw
}

$setupContent = Get-ScriptContent 'scripts/setup-dev.ps1'
Assert 'setup-dev references dev-local-openai.bicep' { $setupContent -match 'dev-local-openai\.bicep' }
Assert 'setup-dev references dev-local.bicep'        { $setupContent -match 'dev-local\.bicep' }
Assert 'setup-dev uses ConvertFrom-Json (not python3)' { $setupContent -match 'ConvertFrom-Json' }
Assert 'setup-dev does NOT use python3'               { $setupContent -notmatch 'python3' }
Assert 'setup-dev writes .env file'                    { $setupContent -match 'Set-Content.*\.env' }
Assert 'setup-dev includes ANTHROPIC_API_KEY in .env'  { $setupContent -match 'ANTHROPIC_API_KEY' }
Assert 'setup-dev uses Read-Host for confirmation'     { $setupContent -match 'Read-Host' }
Assert 'setup-dev sets ErrorActionPreference'          { $setupContent -match "ErrorActionPreference\s*=\s*'Stop'" }

$teardownContent = Get-ScriptContent 'scripts/teardown-dev.ps1'
Assert 'teardown-dev uses --yes --no-wait'             { $teardownContent -match '--yes --no-wait' }
Assert 'teardown-dev uses Read-Host for confirmation'  { $teardownContent -match 'Read-Host' }

$apiDeployContent = Get-ScriptContent 'scripts/api-deploy.ps1'
Assert 'api-deploy uses git rev-parse for tag'         { $apiDeployContent -match 'git rev-parse --short HEAD' }
Assert 'api-deploy runs docker build'                  { $apiDeployContent -match 'docker build' }
Assert 'api-deploy runs docker push'                   { $apiDeployContent -match 'docker push' }
Assert 'api-deploy uses az containerapp update'        { $apiDeployContent -match 'az containerapp update' }

$webDeployContent = Get-ScriptContent 'scripts/web-deploy.ps1'
Assert 'web-deploy sets VITE_SURF_API_URL'             { $webDeployContent -match 'VITE_SURF_API_URL' }
Assert 'web-deploy runs npm run build'                 { $webDeployContent -match 'npm run build' }
Assert 'web-deploy uses Push-Location/Pop-Location'    { $webDeployContent -match 'Push-Location' -and $webDeployContent -match 'Pop-Location' }
Assert 'web-deploy uses npx swa deploy'               { $webDeployContent -match 'npx swa deploy' }

# ── 6. justfile.win checks ───────────────────────────────────────────
Write-Host "`n=== justfile.win ===" -ForegroundColor Cyan

$justfileContent = Get-ScriptContent 'justfile.win'
Assert 'justfile.win sets pwsh shell'                  { $justfileContent -match 'set shell := \["pwsh"' }
Assert 'justfile.win setup-dev calls .ps1 script'      { $justfileContent -match 'scripts/setup-dev\.ps1' }
Assert 'justfile.win teardown-dev calls .ps1 script'   { $justfileContent -match 'scripts/teardown-dev\.ps1' }
Assert 'justfile.win api-deploy calls .ps1 script'     { $justfileContent -match 'scripts/api-deploy\.ps1' }
Assert 'justfile.win web-deploy calls .ps1 script'     { $justfileContent -match 'scripts/web-deploy\.ps1' }
Assert 'justfile.win does NOT use bash shebangs'        { $justfileContent -notmatch '#!/usr/bin/env bash' }
Assert 'justfile.win uses python (not python3)'         { $justfileContent -notmatch 'python3' }

# ── 7. Bicep template references ──────────────────────────────────────
Write-Host "`n=== Bicep Templates Exist ===" -ForegroundColor Cyan

Assert 'infra/dev-local-openai.bicep exists' { Test-Path (Join-Path $RepoRoot 'infra/dev-local-openai.bicep') }
Assert 'infra/dev-local.bicep exists'        { Test-Path (Join-Path $RepoRoot 'infra/dev-local.bicep') }

# ── 8. justfile.win recipe parity ────────────────────────────────────
Write-Host "`n=== Recipe Parity (justfile vs justfile.win) ===" -ForegroundColor Cyan

$originalJustfile = Get-ScriptContent 'justfile'
# Extract recipe names from both files (lines starting with a word followed by colon or with args)
# Match recipe lines (word + optional args + colon) but exclude 'set' directives
$originalRecipes = [regex]::Matches($originalJustfile, '(?m)^(\w[\w-]*)\s*[\*\w]*.*:') | ForEach-Object { $_.Groups[1].Value } | Where-Object { $_ -ne 'set' } | Sort-Object -Unique
$winRecipes      = [regex]::Matches($justfileContent, '(?m)^(\w[\w-]*)\s*[\*\w]*.*:')   | ForEach-Object { $_.Groups[1].Value } | Where-Object { $_ -ne 'set' } | Sort-Object -Unique

Assert "justfile.win has same number of recipes as justfile ($($originalRecipes.Count))" {
    $originalRecipes.Count -eq $winRecipes.Count
}

$missing = $originalRecipes | Where-Object { $_ -notin $winRecipes }
if ($missing) {
    Write-Host "    Missing from justfile.win: $($missing -join ', ')" -ForegroundColor Yellow
}
foreach ($recipe in $originalRecipes) {
    Assert "Recipe '$recipe' exists in justfile.win" { $recipe -in $winRecipes }
}

# ── 9. Documentation checks ──────────────────────────────────────────
Write-Host "`n=== Documentation ===" -ForegroundColor Cyan

$docContent = Get-ScriptContent 'docs/local-deployment.md'
Assert 'docs mention az login'                { $docContent -match 'az login' }
Assert 'docs mention DefaultAzureCredential'  { $docContent -match 'DefaultAzureCredential' }
Assert 'docs mention ANTHROPIC_API_KEY'       { $docContent -match 'ANTHROPIC_API_KEY' }
Assert 'docs mention justfile.win'            { $docContent -match 'justfile\.win' }
Assert 'docs mention uv sync'                 { $docContent -match 'uv sync' }
Assert 'docs mention python vs python3'       { $docContent -match 'python3.*python' -or $docContent -match 'python.*python3' }

# ── Summary ───────────────────────────────────────────────────────────
Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "  Passed: $Pass" -ForegroundColor Green
if ($Fail -gt 0) {
    Write-Host "  Failed: $Fail" -ForegroundColor Red
    exit 1
} else {
    Write-Host "  Failed: 0" -ForegroundColor Green
    Write-Host "`nAll checks passed." -ForegroundColor Green
}
