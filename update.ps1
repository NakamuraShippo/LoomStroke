[CmdletBinding()]
param(
    [string]$Root = $PSScriptRoot,
    [Alias('-no-pause')][switch]$NoPause
)

# Public ZIP installations use an archive; public Git checkouts fast-forward.
$script:UpdateUrl = 'https://codeload.github.com/NakamuraShippo/LoomStroke/zip/refs/heads/main'
$script:ManagedPaths = @(
    'src/loomstroke', 'img', 'docs/user', 'LICENSES',
    'LICENSE', 'LICENSE_JA', 'THIRD_PARTY_LICENSES', 'README.md', 'PUBLICATION.md', 'CHANGELOG.md',
    'pyproject.toml', 'requirements.txt', 'main.py', 'run.sh',
    'install.bat', 'install_python.ps1', 'boot_LoomStroke.bat', 'boot_LoomStroke_noAI.bat',
    'update.bat', 'update.ps1'
)

function Assert-LoomStrokeChildPath {
    param([string]$ParentPath, [string]$ChildPath)
    $parentFull = [IO.Path]::GetFullPath($ParentPath).TrimEnd('\') + '\'
    if (-not [IO.Path]::GetFullPath($ChildPath).StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'An update path escaped its destination.'
    }
}

function Assert-LoomStrokeOrdinaryPath {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw ('Linked update paths are not supported: ' + $Path)
        }
        if ($item.PSIsContainer) {
            foreach ($child in Get-ChildItem -LiteralPath $Path -Force) {
                Assert-LoomStrokeOrdinaryPath $child.FullName
            }
        }
    }
}

function Assert-LoomStrokeStopped {
    param([string]$InstallRoot)
    # Fail closed when process inspection is unavailable. Never terminate an app.
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'LoomStroke.exe'" -ErrorAction Stop
    foreach ($process in $processes) {
        $localPython = $process.ExecutablePath -and
            $process.ExecutablePath.StartsWith($InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase)
        $loomCommand = $process.CommandLine -and (
            $process.CommandLine -match '(?i)(?:^|\s)-m\s+loomstroke(?:\s|$)' -or
            $process.CommandLine.IndexOf($InstallRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0)
        if ($localPython -or $loomCommand -or $process.Name -eq 'LoomStroke.exe') {
            throw 'Close LoomStroke (and Python processes using this folder), then run update.bat again.'
        }
    }
}

function Get-LoomStrokeUpdateArchive {
    param([string]$Destination)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $request = [Net.HttpWebRequest]::Create($script:UpdateUrl)
    $request.Timeout = 120000
    $request.ReadWriteTimeout = 120000
    $response = $request.GetResponse()
    try {
        if ($response.ResponseUri.Scheme -ne 'https') { throw 'Update requires HTTPS.' }
        $inputStream = $response.GetResponseStream()
        $outputStream = [IO.File]::Create($Destination)
        try {
            $buffer = New-Object byte[] 65536
            $total = 0
            while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $total += $read
                if ($total -gt 209715200) { throw 'Update exceeds the 200 MiB download limit.' }
                $outputStream.Write($buffer, 0, $read)
            }
        } finally { $outputStream.Dispose(); $inputStream.Dispose() }
    } finally { $response.Dispose() }
}

function Expand-LoomStrokeUpdate {
    param([string]$Archive, [string]$Payload)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        $seen = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        $entries = @()
        $total = 0L
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName
            if (-not $name.StartsWith('LoomStroke-main/', [StringComparison]::Ordinal)) {
                throw 'Unexpected update archive root.'
            }
            $relative = $name.Substring('LoomStroke-main/'.Length).TrimEnd('/')
            if (-not $relative) { continue }
            foreach ($part in $relative.Split('/')) {
                if (-not $part -or $part -in @('.', '..') -or $part -match '[<>:"\\|?*\x00-\x1f]' -or
                    $part.EndsWith('.') -or $part.EndsWith(' ') -or
                    $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
                    throw 'Unsafe entry in update archive.'
                }
            }
            if (-not $seen.Add($relative) -or (($entry.ExternalAttributes -shr 16) -band 0xf000) -eq 0xa000) {
                throw 'Duplicate or linked entry in update archive.'
            }
            $total += $entry.Length
            if ($total -gt 1073741824 -or $seen.Count -gt 20000) { throw 'Update archive is too large.' }
            if ($name.EndsWith('/')) { continue }
            $managed = $false
            foreach ($path in $script:ManagedPaths) {
                if ($relative -ceq $path -or $relative.StartsWith($path + '/', [StringComparison]::Ordinal)) {
                    $managed = $true
                    break
                }
            }
            if ($managed) { $entries += [PSCustomObject]@{ Entry = $entry; Relative = $relative } }
        }
        foreach ($file in $entries) {
            $target = Join-Path $Payload $file.Relative
            Assert-LoomStrokeChildPath $Payload $target
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) > $null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($file.Entry, $target, $false)
        }
    } finally { $zip.Dispose() }
    foreach ($required in @('src/loomstroke/__init__.py', 'src/loomstroke/__main__.py',
            'pyproject.toml', 'README.md', 'install.bat', 'install_python.ps1',
            'boot_LoomStroke.bat', 'update.bat', 'update.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Payload $required) -PathType Leaf)) {
            throw ('Incomplete update archive: ' + $required)
        }
    }
}

function Install-LoomStrokeUpdateDependencies {
    param([string]$InstallRoot)
    # Reuse the existing interpreter, optional installed SDKs and setup checks.
    Push-Location -LiteralPath $InstallRoot
    try {
        & (Join-Path $InstallRoot 'install.bat') --no-pause
        if ($LASTEXITCODE -ne 0) { throw ('Dependency setup failed (exit ' + $LASTEXITCODE + ').') }
    } finally { Pop-Location }
}

function Invoke-LoomStrokeGit {
    param([string]$Executable, [string]$InstallRoot, [string[]]$Arguments)
    # Windows PowerShell treats native stderr as errors, even for fetch progress.
    $ErrorActionPreference = 'Continue'
    $output = & $Executable -C $InstallRoot @Arguments 2>&1
    $code = $LASTEXITCODE
    $message = (($output | ForEach-Object { $_.ToString() }) -join "`n").Trim()
    if ($code -ne 0) { throw ('Git ' + $Arguments[0] + ' failed: ' + $message) }
    return $message
}

function Update-LoomStrokeGit {
    param([string]$InstallRoot)
    $git = (Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (-not $git) { throw 'Install Git for Windows, then run update.bat again.' }
    try { $top = Invoke-LoomStrokeGit $git $InstallRoot @('rev-parse', '--show-toplevel') }
    catch { throw ('This is not a valid Git checkout. Use Git to repair it before updating. ' + $_.Exception.Message) }
    if ([IO.Path]::GetFullPath($top).TrimEnd('\') -ine $InstallRoot) {
        throw 'Run update.bat from the root of the public Git checkout.'
    }
    $origin = Invoke-LoomStrokeGit $git $InstallRoot @('config', '--get-all', 'remote.origin.url')
    if ($origin -notmatch '^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)NakamuraShippo/LoomStroke(?:\.git)?/?$') {
        throw 'Git updates require origin to point to the public NakamuraShippo/LoomStroke repository.'
    }
    $branch = Invoke-LoomStrokeGit $git $InstallRoot @('rev-parse', '--abbrev-ref', 'HEAD')
    if ($branch -cne 'main') { throw 'Switch to the main branch with Git before running update.bat.' }
    foreach ($operation in @('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply')) {
        $path = Invoke-LoomStrokeGit $git $InstallRoot @('rev-parse', '--git-path', $operation)
        if (-not [IO.Path]::IsPathRooted($path)) { $path = Join-Path $InstallRoot $path }
        if (Test-Path -LiteralPath $path) { throw 'Finish the pending Git operation before updating.' }
    }
    $status = Invoke-LoomStrokeGit $git $InstallRoot @('status', '--porcelain', '--untracked-files=normal')
    if ($status) { throw 'Local changes were found. Preserve your local changes and update manually with Git.' }
    $previous = Invoke-LoomStrokeGit $git $InstallRoot @('rev-parse', 'HEAD')
    Write-Host 'Fetching the latest public LoomStroke Git main...'
    Invoke-LoomStrokeGit $git $InstallRoot @('fetch', '--no-tags', 'origin', 'main') | Out-Host
    $target = Invoke-LoomStrokeGit $git $InstallRoot @('rev-parse', 'FETCH_HEAD')
    $localCommits = Invoke-LoomStrokeGit $git $InstallRoot @('rev-list', '--count', ($target + '..HEAD'))
    if ([int]$localCommits -ne 0) { throw 'Local commits prevent a fast-forward. Update manually with Git.' }
    Assert-LoomStrokeStopped $InstallRoot
    $status = Invoke-LoomStrokeGit $git $InstallRoot @('status', '--porcelain', '--untracked-files=normal')
    if ($status) { throw 'Local changes appeared during download. Update manually with Git.' }
    Write-Host ('Previous Git commit: ' + $previous)
    # Refuse even ignored-file collisions; never stash, reset, or force checkout.
    Invoke-LoomStrokeGit $git $InstallRoot @('merge', '--ff-only', '--no-overwrite-ignore', $target) | Out-Host
    try { Install-LoomStrokeUpdateDependencies $InstallRoot }
    catch { throw ('Git source updated, but dependency setup failed. Run install.bat again. ' + $_.Exception.Message) }
    Write-Host 'Update completed successfully. Start LoomStroke with boot_LoomStroke.bat.'
}

function Update-LoomStroke {
    param([string]$InstallRoot)
    $ErrorActionPreference = 'Stop'
    $rootPath = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    if (-not (Test-Path -LiteralPath (Join-Path $rootPath 'pyproject.toml') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $rootPath 'src/loomstroke') -PathType Container)) {
        throw 'Run update.bat from an extracted LoomStroke source ZIP or public Git checkout.'
    }
    if ((Test-Path -LiteralPath (Join-Path $rootPath 'AGENTS.md')) -or
        (Test-Path -LiteralPath (Join-Path $rootPath 'docs/dev'))) {
        throw 'Development folders are not updated automatically. Use Git to update this checkout.'
    }
    # Check parents too: a junction at src/ or docs/ could escape the installation.
    foreach ($name in @('', 'src', 'docs', '.update-backups')) {
        $path = if ($name) { Join-Path $rootPath $name } else { $rootPath }
        if ((Test-Path -LiteralPath $path) -and
            ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Linked installation folders are not supported.'
        }
    }
    Assert-LoomStrokeStopped $rootPath
    $lockPath = Join-Path $rootPath '.loomstroke-update.lock'
    Assert-LoomStrokeOrdinaryPath $lockPath
    try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another update is running, or this folder is not writable.' }
    $stage = Join-Path $rootPath ('.loomstroke-update-' + [Guid]::NewGuid().ToString('N'))
    $backup = Join-Path $rootPath ('.update-backups/' + (Get-Date -Format 'yyyyMMdd-HHmmss-') + [Guid]::NewGuid().ToString('N'))
    $changes = New-Object 'Collections.Generic.List[object]'
    try {
        if (Test-Path -LiteralPath (Join-Path $rootPath '.git')) {
            Update-LoomStrokeGit $rootPath
            return
        }
        [IO.Directory]::CreateDirectory($stage) > $null
        $archive = Join-Path $stage 'update.zip'
        $payload = Join-Path $stage 'payload'
        Write-Host 'Downloading the latest public LoomStroke ZIP...'
        Get-LoomStrokeUpdateArchive $archive
        Write-Host 'Checking and extracting the update...'
        Expand-LoomStrokeUpdate $archive $payload
        $targets = @($script:ManagedPaths | Where-Object { Test-Path -LiteralPath (Join-Path $payload $_) })
        foreach ($relative in $script:ManagedPaths) {
            Assert-LoomStrokeOrdinaryPath (Join-Path $rootPath $relative)
        }
        Assert-LoomStrokeStopped $rootPath
        [IO.Directory]::CreateDirectory($backup) > $null
        [IO.File]::WriteAllText((Join-Path $backup 'RECOVERY.txt'),
            "Previous app files are in this folder, with their original relative paths.`r`n" +
            "Keep user_data, .venv, venv, .runtime and your documents in the installation folder.`r`n" +
            "After closing LoomStroke, restore the paths listed in changes.json to the installation folder.`r`n" +
            "If dependency setup failed, run install.bat again; Python packages are not backed up.`r`n")
        Write-Host ('Recovery backup: ' + $backup)
        try {
            foreach ($relative in $targets) {
                $target = Join-Path $rootPath $relative
                $previous = Join-Path $backup $relative
                $incoming = Join-Path $payload $relative
                Assert-LoomStrokeChildPath $rootPath $target
                Assert-LoomStrokeChildPath $backup $previous
                $change = [PSCustomObject]@{ Path = $relative; Saved = $false; Installing = $false }
                $changes.Add($change)
                if (Test-Path -LiteralPath $target) {
                    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($previous)) > $null
                    Move-Item -LiteralPath $target -Destination $previous
                    $change.Saved = $true
                }
                $change.Installing = $true
                $changes | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $backup 'changes.json') -Encoding UTF8
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) > $null
                Move-Item -LiteralPath $incoming -Destination $target
            }
            Write-Host 'Updating required Python packages...'
            Install-LoomStrokeUpdateDependencies $rootPath
        } catch {
            $updateError = $_
            Write-Warning 'Update failed. Restoring previous app files...'
            # Reverse only paths this transaction actually touched.
            for ($index = $changes.Count - 1; $index -ge 0; $index--) {
                $change = $changes[$index]
                $target = Join-Path $rootPath $change.Path
                $previous = Join-Path $backup $change.Path
                Assert-LoomStrokeChildPath $rootPath $target
                if ($change.Installing -and (Test-Path -LiteralPath $target)) {
                    Remove-Item -LiteralPath $target -Recurse -Force
                }
                if ($change.Saved) { Move-Item -LiteralPath $previous -Destination $target }
            }
            throw ('Update failed; previous app files restored. Python packages may need install.bat. ' + $updateError.Exception.Message)
        }
        Write-Host ('Previous app files: ' + $backup)
        Write-Host 'Update completed successfully. Start LoomStroke with boot_LoomStroke.bat.'
    } finally {
        Assert-LoomStrokeChildPath $rootPath $stage
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        $lock.Dispose()
    }
}

# Dot-sourcing permits offline tests without changing the public download source.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Update-LoomStroke -InstallRoot $Root
        exit 0
    } catch {
        Write-Error ('LoomStroke update failed: ' + $_.Exception.Message)
        exit 1
    }
}
