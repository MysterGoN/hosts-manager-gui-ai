[CmdletBinding()]
param(
    [string]$ReleaseBaseUrl = "https://github.com/MysterGoN/hosts-manager-gui/releases/latest/download",
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\HostsManagerGUI"),
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$archiveName = "hosts-manager-gui-windows.zip"
$temporaryDir = Join-Path ([System.IO.Path]::GetTempPath()) ("hmg-install-" + [guid]::NewGuid())

New-Item -ItemType Directory -Force $temporaryDir | Out-Null
try {
    $archivePath = Join-Path $temporaryDir $archiveName
    $checksumsPath = Join-Path $temporaryDir "SHA256SUMS"
    Invoke-WebRequest "$ReleaseBaseUrl/$archiveName" -OutFile $archivePath
    Invoke-WebRequest "$ReleaseBaseUrl/SHA256SUMS" -OutFile $checksumsPath

    $checksumLine = Get-Content $checksumsPath |
        Where-Object { $_ -match " $([regex]::Escape($archiveName))$" } |
        Select-Object -First 1
    if (-not $checksumLine) {
        throw "Checksum for $archiveName is missing."
    }

    $expectedHash = ($checksumLine -split "\s+")[0]
    $actualHash = (Get-FileHash $archivePath -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) {
        throw "Archive SHA-256 mismatch."
    }

    $extractedDir = Join-Path $temporaryDir "extracted"
    Expand-Archive $archivePath -DestinationPath $extractedDir -Force
    $sourceExecutable = Join-Path $extractedDir "hosts-manager-gui.exe"
    if (-not (Test-Path -LiteralPath $sourceExecutable -PathType Leaf)) {
        throw "The archive does not contain hosts-manager-gui.exe."
    }

    New-Item -ItemType Directory -Force $InstallDir | Out-Null
    $installedExecutable = Join-Path $InstallDir "hosts-manager-gui.exe"
    Copy-Item -LiteralPath $sourceExecutable -Destination $installedExecutable -Force

    $programsDir = [Environment]::GetFolderPath("Programs")
    $shortcutPath = Join-Path $programsDir "Hosts Manager GUI.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $installedExecutable
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.Save()

    Write-Host "Hosts Manager GUI installed to $installedExecutable"
    if (-not $NoLaunch) {
        Start-Process -FilePath $installedExecutable
    }
}
finally {
    Remove-Item -LiteralPath $temporaryDir -Recurse -Force -ErrorAction SilentlyContinue
}
