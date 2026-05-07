param(
    [string]$Remote = "mac",
    [string]$Branch = "main",
    [string]$Message = "",
    [string]$SourceAppRoot = "",
    [switch]$SkipTests,
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"

function Run-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Run-Step {
    param(
        [string]$Label,
        [scriptblock]$Block
    )
    Write-Host "==> $Label"
    & $Block
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Resolve-Directory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Directory not found: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Ensure-UnderPath {
    param(
        [string]$Child,
        [string]$Parent,
        [string]$Label
    )
    $childPath = [System.IO.Path]::GetFullPath($Child)
    $parentPath = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\', '/')
    if (-not ($childPath -eq $parentPath -or $childPath.StartsWith($parentPath + [System.IO.Path]::DirectorySeparatorChar))) {
        throw "$Label is outside the allowed root. Path: $childPath Root: $parentPath"
    }
}

function Ensure-FinancialServicesLink {
    param([string]$RepoRoot)
    $expected = Join-Path $RepoRoot "financial-services"
    if (Test-Path -LiteralPath $expected) {
        return
    }

    $sibling = Join-Path (Split-Path -Parent $RepoRoot) "financial-services"
    if (-not (Test-Path -LiteralPath $sibling -PathType Container)) {
        Write-Host "financial-services not found next to repo; tests that load skills may fail."
        return
    }

    Write-Host "Creating local financial-services junction for tests: $expected"
    New-Item -ItemType Junction -Path $expected -Target $sibling | Out-Null

    $excludePath = (& git rev-parse --git-path info/exclude).Trim()
    if (Test-Path -LiteralPath $excludePath) {
        $excludeText = Get-Content -LiteralPath $excludePath -Raw
        if ($excludeText -notmatch "(?m)^financial-services/$") {
            Add-Content -LiteralPath $excludePath -Value "financial-services/"
        }
    }
}

function Sync-AppRoot {
    param(
        [string]$SourceRoot,
        [string]$TargetRoot
    )

    $source = Resolve-Directory $SourceRoot
    $target = Resolve-Directory $TargetRoot
    Ensure-UnderPath -Child $target -Parent (Resolve-Directory (Split-Path -Parent $TargetRoot)) -Label "Target app root"

    $items = @(
        ".gitignore",
        ".env.example",
        "pyproject.toml",
        "README.md",
        "docs",
        "tests",
        "trading_copilot"
    )

    foreach ($item in $items) {
        $src = Join-Path $source $item
        $dst = Join-Path $target $item
        if (-not (Test-Path -LiteralPath $src)) {
            continue
        }

        if (Test-Path -LiteralPath $src -PathType Container) {
            Write-Host "Syncing directory $item"
            $robocopyArgs = @(
                $src,
                $dst,
                "/MIR",
                "/XD", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git", "out", "data",
                "/XF", "*.pyc", "*.pyo",
                "/NFL", "/NDL", "/NJH", "/NJS", "/NP"
            )
            & robocopy @robocopyArgs | Out-Host
            if ($LASTEXITCODE -gt 7) {
                throw "robocopy failed for $item with exit code $LASTEXITCODE"
            }
            $global:LASTEXITCODE = 0
        }
        else {
            Write-Host "Syncing file $item"
            Copy-Item -LiteralPath $src -Destination $dst -Force
        }
    }
}

$repoRoot = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
    throw "Run this script inside the mac repository."
}
$repoRoot = Resolve-Directory $repoRoot
$appRoot = Join-Path $repoRoot "trading-copilot"
if (-not (Test-Path -LiteralPath $appRoot -PathType Container)) {
    throw "Expected trading-copilot directory under mac repo: $appRoot"
}

Push-Location $repoRoot
try {
    Ensure-FinancialServicesLink -RepoRoot $repoRoot

    if ($SourceAppRoot.Trim()) {
        Sync-AppRoot -SourceRoot $SourceAppRoot -TargetRoot $appRoot
    }

    Run-Git fetch $Remote --prune

    if (-not $SkipTests) {
        Push-Location $appRoot
        try {
            Run-Step "unit tests" { python -m unittest discover -s tests }
            $cacheRoot = Join-Path $env:TEMP ("trading_copilot_pycache_" + [Guid]::NewGuid().ToString("N"))
            $env:PYTHONPYCACHEPREFIX = $cacheRoot
            try {
                Run-Step "compileall" { python -m compileall -q trading_copilot }
            }
            finally {
                Remove-Item -LiteralPath $cacheRoot -Recurse -Force -ErrorAction SilentlyContinue
                Remove-Item Env:\PYTHONPYCACHEPREFIX -ErrorAction SilentlyContinue
            }
        }
        finally {
            Pop-Location
        }
    }

    Run-Git diff --check
    Run-Git add -- trading-copilot

    $cachedDiff = (& git diff --cached --name-only)
    if ($cachedDiff) {
        if (-not $Message.Trim()) {
            $Message = "Update trading copilot"
        }
        Run-Git commit -m $Message
    }
    else {
        Write-Host "No trading-copilot changes to commit."
    }

    Run-Git fetch $Remote --prune
    & git merge-base --is-ancestor "$Remote/$Branch" HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "HEAD is not a fast-forward of $Remote/$Branch. Pull/rebase first; refusing to push."
    }

    if ($NoPush) {
        Write-Host "NoPush set; not pushing."
    }
    else {
        Run-Git push $Remote "HEAD:$Branch"
    }
}
finally {
    Pop-Location
}
