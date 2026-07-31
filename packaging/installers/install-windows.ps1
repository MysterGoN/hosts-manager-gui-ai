[CmdletBinding()]
param(
    [string]$ReleaseBaseUrl = "https://github.com/MysterGoN/hosts-manager-gui/releases/latest/download",
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\HostsManagerGUI"),
    [string]$ArchivePath = "",
    [string]$ChecksumsPath = "",
    [int]$WaitForProcessId = 0,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$archiveName = "hosts-manager-gui-windows.zip"
$temporaryDir = Join-Path ([System.IO.Path]::GetTempPath()) ("hmg-install-" + [guid]::NewGuid())

New-Item -ItemType Directory -Force $temporaryDir | Out-Null
try {
    $localArchivePath = Join-Path $temporaryDir $archiveName
    $localChecksumsPath = Join-Path $temporaryDir "SHA256SUMS"
    if ($ArchivePath -or $ChecksumsPath) {
        if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $ChecksumsPath -PathType Leaf)) {
            throw "Both ArchivePath and ChecksumsPath must reference files."
        }
        Copy-Item -LiteralPath $ArchivePath -Destination $localArchivePath
        Copy-Item -LiteralPath $ChecksumsPath -Destination $localChecksumsPath
    }
    else {
        Invoke-WebRequest "$ReleaseBaseUrl/$archiveName" -OutFile $localArchivePath
        Invoke-WebRequest "$ReleaseBaseUrl/SHA256SUMS" -OutFile $localChecksumsPath
    }

    $checksumLine = Get-Content $localChecksumsPath |
        Where-Object { $_ -match " $([regex]::Escape($archiveName))$" } |
        Select-Object -First 1
    if (-not $checksumLine) {
        throw "Checksum for $archiveName is missing."
    }

    $expectedHash = ($checksumLine -split "\s+")[0]
    $actualHash = (Get-FileHash $localArchivePath -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) {
        throw "Archive SHA-256 mismatch."
    }

    $extractedDir = Join-Path $temporaryDir "extracted"
    Expand-Archive $localArchivePath -DestinationPath $extractedDir -Force
    $sourceExecutable = Join-Path $extractedDir "hosts-manager-gui.exe"
    if (-not (Test-Path -LiteralPath $sourceExecutable -PathType Leaf)) {
        throw "The archive does not contain hosts-manager-gui.exe."
    }

    if ($WaitForProcessId -gt 0 -and $WaitForProcessId -ne $PID) {
        $waitDeadline = [DateTime]::UtcNow.AddMinutes(2)
        while (Get-Process -Id $WaitForProcessId -ErrorAction SilentlyContinue) {
            if ([DateTime]::UtcNow -ge $waitDeadline) {
                throw "Timed out waiting for the running application to close."
            }
            Start-Sleep -Milliseconds 200
        }
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
