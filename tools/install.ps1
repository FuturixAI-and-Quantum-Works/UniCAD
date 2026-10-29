<#
.SYNOPSIS
  Install or update UniCAD (the unicad-mcp server, LibreCAD 2.2.1.6 and LibreDWG 0.13.4) for the current
  Windows user, without admin rights, and register it with Claude Code and Claude Desktop.

.DESCRIPTION
  One command, from any PowerShell window (the repository is private, so a GitHub token is needed):

    $env:GH_TOKEN = "<token>"
    irm https://raw.githubusercontent.com/Aqua-123/unicad/master/tools/install.ps1 -Headers @{Authorization="token $env:GH_TOKEN"} | iex

  Everything goes under %LOCALAPPDATA%\UniCAD; projects go to %USERPROFILE%\unicad-projects. Re-running updates
  UniCAD and skips the parts that are already installed. With parameters (a branch, a local checkout, uninstall):

    & ([scriptblock]::Create((irm <same url> -Headers @{Authorization="token $env:GH_TOKEN"}))) -Ref win-native
    .\tools\install.ps1 -Source C:\path\to\checkout
    .\tools\install.ps1 -Uninstall
#>
param(
    [string]$Source = $env:UNICAD_SOURCE,
    [string]$Ref = $(if ($env:UNICAD_REF) { $env:UNICAD_REF } else { "master" }),
    [string]$Repo = "Aqua-123/unicad",
    [switch]$Uninstall
)

$LibreCadVersion = "2.2.1.6"
$LibreCadAsset = "LibreCAD-v$LibreCadVersion-win64-msvc.exe"
$LibreCadSha256 = "0cd876d0e3fbfcdcdb85e5019080aca243908f4ad5c3b3c93a55ee9ba154f95b"
$LibreDwgVersion = "0.13.4"
$LibreDwgAsset = "libredwg-$LibreDwgVersion-win64.zip"
$LibreDwgSha256 = "cb46bce034296e91cb1a982cd53ec1928b11f4f7f70512dd21513a27959688b5"

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [switch]$AllowFailure)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        throw "$([IO.Path]::GetFileName($Exe)) $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Save-Url {
    param([string]$Url, [string]$OutFile, [hashtable]$Headers = @{})
    $Headers["User-Agent"] = "unicad-install"
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -Headers $Headers -UseBasicParsing
}

function Test-Sha256 {
    param([string]$File, [string]$Expected, [string]$SumsUrl, [string]$Name)
    $published = $null
    try {
        $sums = (Invoke-WebRequest -Uri $SumsUrl -UseBasicParsing -Headers @{ "User-Agent" = "unicad-install" }).Content
        if ($sums -is [byte[]]) { $sums = [Text.Encoding]::ASCII.GetString($sums) }
        foreach ($line in $sums -split "`n") {
            $parts = $line.Trim() -split "\s+"
            if ($parts.Count -ge 2 -and $parts[-1].TrimStart("*") -eq $Name) { $published = $parts[0].ToLower() }
        }
    } catch {
        Write-Warning "could not fetch $SumsUrl ($($_.Exception.Message)); checking against the pinned hash only"
    }
    if ($published -and $published -ne $Expected) {
        throw "$Name is listed with SHA256 $published in $SumsUrl but this installer pins $Expected; refusing to continue"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -Path $File).Hash.ToLower()
    if ($actual -ne $Expected) {
        Remove-Item $File -Force
        throw "$Name has SHA256 $actual, expected $Expected (download corrupted or tampered with)"
    }
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($dir in @("$env:USERPROFILE\.local\bin", "$env:USERPROFILE\.cargo\bin", "$env:LOCALAPPDATA\Programs\uv")) {
        if (Test-Path "$dir\uv.exe") { return "$dir\uv.exe" }
    }
    return $null
}

function Find-Claude {
    $cmd = Get-Command claude -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return $null
}

function Invoke-Quietly([scriptblock]$Block) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block 2>&1 | Out-Null } catch { } finally { $ErrorActionPreference = $saved }
}

function Add-SessionPath([string]$Dir) {
    if ($Dir -and (($env:Path -split ";") -notcontains $Dir)) { $env:Path = "$Dir;$env:Path" }
}

function Get-DesktopConfigs {
    $paths = @()
    if (Test-Path "$env:APPDATA\Claude") { $paths += "$env:APPDATA\Claude\claude_desktop_config.json" }
    Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue | ForEach-Object {
        $dir = Join-Path $_.FullName "LocalCache\Roaming\Claude"
        if (Test-Path $dir) { $paths += (Join-Path $dir "claude_desktop_config.json") }
    }
    return $paths
}

function Write-Utf8([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding $false))
}

function Update-DesktopConfig {
    param([string]$Path, [object]$Entry)
    $config = $null
    if (Test-Path $Path) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        Copy-Item $Path "$Path.bak-$stamp"
        $text = [IO.File]::ReadAllText($Path)
        if ($text.Trim()) { $config = $text | ConvertFrom-Json }
    }
    if (-not $config) { $config = New-Object PSObject }
    if (-not ($config.PSObject.Properties.Name -contains "mcpServers") -or -not $config.mcpServers) {
        $config | Add-Member -NotePropertyName mcpServers -NotePropertyValue (New-Object PSObject) -Force
    }
    if ($null -eq $Entry) {
        $config.mcpServers.PSObject.Properties.Remove("unicad")
    } else {
        $config.mcpServers | Add-Member -NotePropertyName unicad -NotePropertyValue $Entry -Force
    }
    Write-Utf8 $Path ($config | ConvertTo-Json -Depth 32)
}

function Uninstall-UniCAD {
    $root = Join-Path $env:LOCALAPPDATA "UniCAD"
    $uv = Find-Uv
    if ($uv) { Invoke-Native $uv @("tool", "uninstall", "unicad") -AllowFailure }
    $claude = Find-Claude
    if ($claude) { Invoke-Quietly { & $claude mcp remove --scope user unicad } }
    foreach ($path in Get-DesktopConfigs) { if (Test-Path $path) { Update-DesktopConfig -Path $path -Entry $null } }
    foreach ($name in @("LIBRECAD", "LIBREDWG_BIN", "UNICAD_WORKSPACE")) { [Environment]::SetEnvironmentVariable($name, $null, "User") }
    if (Test-Path $root) { Remove-Item $root -Recurse -Force }
    Write-Host "UniCAD removed. Your projects in $env:USERPROFILE\unicad-projects and uv itself were left in place."
}

function Install-UniCAD {
    param([string]$Source, [string]$Ref, [string]$Repo)
    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $root = Join-Path $env:LOCALAPPDATA "UniCAD"
    $downloads = Join-Path $root "downloads"
    $workspace = Join-Path $env:USERPROFILE "unicad-projects"
    New-Item -ItemType Directory -Force -Path $root, $downloads, $workspace | Out-Null
    $done = New-Object System.Collections.Generic.List[string]

    if (Get-Process -Name unicad-mcp -ErrorAction SilentlyContinue) {
        throw "unicad-mcp is running (Claude Desktop or a Claude Code session uses it). Quit those, then run the installer again."
    }

    Write-Step "uv"
    $uv = Find-Uv
    if (-not $uv) {
        $shell = (Get-Process -Id $PID).Path
        & $shell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        $uv = Find-Uv
        if (-not $uv) { throw "uv was installed but uv.exe was not found; open a new PowerShell window and run the installer again" }
        $done.Add("installed uv ($uv)")
    } else {
        $done.Add("uv already present ($uv)")
    }
    Add-SessionPath (Split-Path $uv)

    Write-Step "UniCAD source"
    if ($Source) {
        $src = (Resolve-Path $Source).Path
        $done.Add("source: local checkout $src")
    } else {
        $headers = @{ Accept = "application/vnd.github+json" }
        if ($env:GH_TOKEN) { $headers["Authorization"] = "Bearer $env:GH_TOKEN" }
        $zip = Join-Path $downloads "unicad-src.zip"
        try {
            Save-Url -Url "https://api.github.com/repos/$Repo/zipball/$Ref" -OutFile $zip -Headers $headers
        } catch {
            $hint = if ($env:GH_TOKEN) { "check that GH_TOKEN can read $Repo and that '$Ref' exists" } else { "set `$env:GH_TOKEN to a token that can read $Repo" }
            throw "could not download $Repo@$Ref ($($_.Exception.Message)): $hint"
        }
        $unpack = Join-Path $root "src-new"
        if (Test-Path $unpack) { Remove-Item $unpack -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $unpack -Force
        $top = Get-ChildItem $unpack -Directory | Select-Object -First 1
        $src = Join-Path $root "src"
        if (Test-Path $src) { Remove-Item $src -Recurse -Force }
        Move-Item $top.FullName $src
        Remove-Item $unpack -Recurse -Force
        Remove-Item $zip -Force
        $done.Add("source: $Repo@$Ref -> $src")
    }

    Write-Step "unicad-mcp (uv tool install)"
    Invoke-Native $uv @("tool", "install", "--force", "--python", "3.12", $src)
    Invoke-Native $uv @("tool", "update-shell") -AllowFailure
    $toolBin = (& $uv tool dir --bin).Trim()
    Add-SessionPath $toolBin
    $server = Join-Path $toolBin "unicad-mcp.exe"
    if (-not (Test-Path $server)) { throw "uv tool install finished but $server is missing" }
    $done.Add("unicad-mcp -> $server")

    Write-Step "LibreCAD $LibreCadVersion"
    $lcDir = Join-Path $root "librecad"
    $lcMarker = Join-Path $lcDir ".unicad-version"
    $lcExe = Join-Path $lcDir "librecad.exe"
    if ((Test-Path $lcExe) -and (Test-Path $lcMarker) -and ((Get-Content $lcMarker -Raw).Trim() -eq $LibreCadVersion)) {
        $done.Add("LibreCAD $LibreCadVersion already in $lcDir")
    } else {
        $setup = Join-Path $downloads $LibreCadAsset
        $base = "https://github.com/LibreCAD/LibreCAD/releases/download/v$LibreCadVersion"
        if (-not (Test-Path $setup) -or (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLower() -ne $LibreCadSha256) {
            Save-Url -Url "$base/$LibreCadAsset" -OutFile $setup
        }
        Test-Sha256 -File $setup -Expected $LibreCadSha256 -SumsUrl "$base/SHA256SUMS" -Name $LibreCadAsset
        if (Test-Path $lcDir) { Remove-Item $lcDir -Recurse -Force }
        # The NSIS installer asks for admin only to write machine-wide registry keys and shortcuts; run as the
        # invoking user so the files land in our directory without a UAC prompt.
        $previous = $env:__COMPAT_LAYER
        $env:__COMPAT_LAYER = "RunAsInvoker"
        try {
            $proc = Start-Process -FilePath $setup -ArgumentList "/S /D=$lcDir" -Wait -PassThru
        } finally {
            $env:__COMPAT_LAYER = $previous
        }
        if (-not (Test-Path $lcExe)) {
            throw "the LibreCAD installer exited with $($proc.ExitCode) and left no $lcExe"
        }
        Write-Utf8 $lcMarker $LibreCadVersion
        $done.Add("installed LibreCAD $LibreCadVersion into $lcDir (SHA256 verified)")
    }

    Write-Step "LibreDWG $LibreDwgVersion"
    $dwgDir = Join-Path $root "libredwg"
    $dwgMarker = Join-Path $dwgDir ".unicad-version"
    if ((Test-Path "$dwgDir\dxf2dwg.exe") -and (Test-Path $dwgMarker) -and ((Get-Content $dwgMarker -Raw).Trim() -eq $LibreDwgVersion)) {
        $done.Add("LibreDWG $LibreDwgVersion already in $dwgDir")
    } else {
        $zip = Join-Path $downloads $LibreDwgAsset
        $base = "https://github.com/LibreDWG/libredwg/releases/download/$LibreDwgVersion"
        Save-Url -Url "$base/$LibreDwgAsset" -OutFile $zip
        Test-Sha256 -File $zip -Expected $LibreDwgSha256 -SumsUrl "$base/dist.sha256" -Name $LibreDwgAsset
        if (Test-Path $dwgDir) { Remove-Item $dwgDir -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $dwgDir -Force
        if (-not (Test-Path "$dwgDir\dxf2dwg.exe")) { throw "$LibreDwgAsset did not contain dxf2dwg.exe" }
        Write-Utf8 $dwgMarker $LibreDwgVersion
        $done.Add("installed LibreDWG $LibreDwgVersion into $dwgDir (SHA256 verified)")
    }

    Write-Step "environment"
    $envVars = [ordered]@{ LIBRECAD = $lcExe; LIBREDWG_BIN = $dwgDir; UNICAD_WORKSPACE = $workspace }
    foreach ($name in $envVars.Keys) {
        [Environment]::SetEnvironmentVariable($name, $envVars[$name], "User")
        Set-Item -Path "Env:$name" -Value $envVars[$name]
    }
    $done.Add("user environment: LIBRECAD, LIBREDWG_BIN, UNICAD_WORKSPACE=$workspace")

    Write-Step "MCP clients"
    $claude = Find-Claude
    if ($claude) {
        Invoke-Quietly { & $claude mcp remove --scope user unicad }
        $claudeArgs = @("mcp", "add", "--scope", "user", "unicad")
        foreach ($name in $envVars.Keys) { $claudeArgs += @("-e", "$name=$($envVars[$name])") }
        $claudeArgs += @("--", $server)
        & $claude @claudeArgs
        if ($LASTEXITCODE -eq 0) { $done.Add("Claude Code: registered 'unicad' (user scope)") }
        else { Write-Warning "claude mcp add failed (exit $LASTEXITCODE); register by hand, see docs/install.md" }
    } else {
        $done.Add("Claude Code: 'claude' not on PATH, skipped")
    }
    $desktop = Get-DesktopConfigs
    if ($desktop) {
        $entry = [ordered]@{ command = $server; args = @(); env = $envVars }
        foreach ($path in $desktop) {
            Update-DesktopConfig -Path $path -Entry $entry
            $done.Add("Claude Desktop: 'unicad' in $path (previous file backed up next to it)")
        }
    } else {
        $done.Add("Claude Desktop: not installed (no %APPDATA%\Claude), skipped")
    }

    Write-Step "self-check"
    Invoke-Native $server @("--help") | Out-Null
    $python = Join-Path (& $uv tool dir).Trim() "unicad\Scripts\python.exe"
    $check = Join-Path $root "self-check"
    if (Test-Path $check) { Remove-Item $check -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $check | Out-Null
    $script = Join-Path $check "check.py"
    Write-Utf8 $script @'
import sys
from pathlib import Path

import ezdxf
import pymupdf

from unicad.render import DwgConverter, LibreCadRenderer

out = Path(sys.argv[1])
doc = ezdxf.new("R2018")
doc.units = 4
doc.layers.get("0").dxf.lineweight = 25
msp = doc.modelspace()
msp.add_lwpolyline([(10, 10), (287, 10), (287, 200), (10, 200)], close=True)
msp.add_text("UNICAD SELF-CHECK", height=7, dxfattribs={"insert": (40, 100)})
doc.saveas(out / "check.dxf")
pdf = LibreCadRenderer(timeout=180).to_pdf(out / "check.dxf", out / "check.pdf", "A4")
with pymupdf.open(pdf) as rendered:
    assert len(rendered) == 1 and rendered[0].get_drawings(), "LibreCAD wrote an empty page"
dwg = DwgConverter("libredwg").to_dwg(out / "check.dxf", out / "check.dwg", strict=False)
print(f"PDF {pdf.stat().st_size} bytes, DWG {dwg.dwg.stat().st_size} bytes")
'@
    $result = & $python $script $check
    if ($LASTEXITCODE -ne 0) { throw "self-check render failed; see the error above (files in $check)" }
    $done.Add("self-check: unicad-mcp --help ok; DXF -> PDF and DWG ok ($result)")

    Write-Host ""
    Write-Host "UniCAD is installed." -ForegroundColor Green
    foreach ($line in $done) { Write-Host "  - $line" }
    Write-Host ""
    Write-Host "Use it:"
    Write-Host "  - Restart Claude Desktop (or start a new Claude Code session) and ask for a drawing set, e.g."
    Write-Host "    'Create a 500 kWp rooftop project for Demo Industries and render its SLD as PDF'."
    Write-Host "  - Drawings are written under $workspace\<project>\drawings."
    Write-Host "  - Other MCP clients: command '$server' with the environment variables above."
    Write-Host "  - Update: run the same install command again. Remove: run it with -Uninstall."
    Write-Host "  - Open a new terminal to pick up PATH and the environment variables."
}

if ($Uninstall) {
    Uninstall-UniCAD
} else {
    Install-UniCAD -Source $Source -Ref $Ref -Repo $Repo
}
