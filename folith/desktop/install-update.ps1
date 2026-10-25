# Runs outside the application directory, after Folith accepts normal shutdown.
param(
    [Parameter(Mandatory=$true)][int]$ParentId,
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$Sha256,
    [Parameter(Mandatory=$true)][string]$TargetVersion,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$DataDir,
    [Parameter(Mandatory=$true)][string]$LogDir
)
$ErrorActionPreference = 'Stop'
$installPath = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$backupPath = $installPath + '.update-backup'
$failedPath = $installPath + '.update-failed'
$logPath = Join-Path $LogDir 'update.log'
$resultPath = Join-Path $LogDir 'update-result.json'
$backedUp = $false
$backupStarted = $false
function Write-UpdateLog([string]$message) {
    Add-Content -LiteralPath $logPath -Value ((Get-Date -Format o) + ' ' + $message)
}
try {
    # Wait on the process object, not process name; never terminate a user task.
    $parentProcess = Get-Process -Id $ParentId -ErrorAction SilentlyContinue
    if ($parentProcess) { $parentProcess.WaitForExit() }
    $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
    $packageStream = [IO.File]::OpenRead($Installer)
    try { $actualHash = [BitConverter]::ToString($hashAlgorithm.ComputeHash($packageStream)).Replace('-', '').ToLowerInvariant() }
    finally { $packageStream.Dispose(); $hashAlgorithm.Dispose() }
    if ($actualHash -ne $Sha256) {
        throw 'Update package validation failed'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $installPath 'Folith.exe'))) {
        throw 'The original application is missing'
    }
    $dataPath = [IO.Path]::GetFullPath($DataDir).TrimEnd('\')
    if ($dataPath -eq $installPath -or $dataPath.StartsWith($installPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'User data must be outside the application directory'
    }
    $otherInstances = Get-Process -Name Folith -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -eq (Join-Path $installPath 'Folith.exe')
    }
    if ($otherInstances) { throw 'Another Folith instance is still using this installation' }
    if ((Test-Path -LiteralPath $backupPath) -or (Test-Path -LiteralPath $failedPath)) {
        throw 'A previous update needs recovery; see update.log'
    }
    $requiredBytes = (Get-ChildItem -LiteralPath $installPath -File -Recurse | Measure-Object -Property Length -Sum).Sum * 3
    $drive = New-Object IO.DriveInfo ([IO.Path]::GetPathRoot($installPath))
    if ($drive.AvailableFreeSpace -lt $requiredBytes) { throw 'Not enough space for update and recovery backup' }
    # A complete backup survives installer failure or an interrupted update.
    Write-UpdateLog 'Backing up application'
    $backupStarted = $true
    Copy-Item -LiteralPath $installPath -Destination $backupPath -Recurse
    $backedUp = $true
    @{ state='installing'; backup=$backupPath; version=$TargetVersion } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-UpdateLog 'Installing update'
    # NSIS requires /D= last, without quotes (even when the path has spaces).
    $process = Start-Process -FilePath $Installer -ArgumentList ('/S /D=' + $installPath) -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -ne 0) { throw ('Installer exit code ' + $process.ExitCode) }
    if (-not (Test-Path -LiteralPath (Join-Path $installPath 'Folith.exe'))) { throw 'Updated application is missing' }
    $installedVersion = (Get-Item -LiteralPath (Join-Path $installPath 'Folith.exe')).VersionInfo.ProductVersion
    if ($installedVersion -ne $TargetVersion) { throw 'Installed version does not match the update' }
    @{ state='installed'; backup=$backupPath; version=$TargetVersion } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-UpdateLog ('Installed; recovery backup retained at ' + $backupPath)
} catch {
    $failure = $_.Exception.Message
    Write-UpdateLog $failure
    if ($backedUp) {
        # Every move target is a verified sibling of the named installation.
        if (Test-Path -LiteralPath $installPath) { Move-Item -LiteralPath $installPath -Destination $failedPath }
        Move-Item -LiteralPath $backupPath -Destination $installPath
    } elseif ($backupStarted -and (Test-Path -LiteralPath $backupPath)) {
        # This exact absolute sibling did not exist before our failed copy.
        if ([IO.Path]::GetDirectoryName($backupPath) -ne [IO.Path]::GetDirectoryName($installPath)) {
            throw 'Unexpected backup location'
        }
        Remove-Item -LiteralPath $backupPath -Recurse -Force
    }
    @{ state='failed'; message=$failure } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
}
if (Test-Path -LiteralPath (Join-Path $installPath 'Folith.exe')) {
    # Preserve the effective data/log locations, including a custom data folder.
    $startArguments = '--data-dir "' + $DataDir + '" --log-dir "' + $LogDir + '"'
    Start-Process -FilePath (Join-Path $installPath 'Folith.exe') -ArgumentList $startArguments -WorkingDirectory $installPath -WindowStyle Normal
}
