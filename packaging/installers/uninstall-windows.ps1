[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\HostsManagerGUI")
)

$ErrorActionPreference = "Stop"
$programsDir = [Environment]::GetFolderPath("Programs")
$shortcutPath = Join-Path $programsDir "Hosts Manager GUI.lnk"
$installedExecutable = Join-Path $InstallDir "hosts-manager-gui.exe"

if (Test-Path -LiteralPath $InstallDir) {
    if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) {
        throw "Refusing to remove a directory that does not contain hosts-manager-gui.exe: $InstallDir"
    }
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
}
if (Test-Path -LiteralPath $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
}

Write-Host "Hosts Manager GUI application files removed."
Write-Host "Settings, local state, logs, backups, and the system hosts file were not changed."
