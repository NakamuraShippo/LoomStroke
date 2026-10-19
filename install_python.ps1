[CmdletBinding()]
param([string]$RuntimeRoot = (Join-Path $PSScriptRoot '.runtime'))

# Official CPython NuGet distribution; pin both its release and payload hash.
# https://docs.python.org/3.13/using/windows.html#the-nuget-org-packages
$script:PythonVersion = '3.13.16'
$script:PythonPackageUrl = 'https://api.nuget.org/v3-flatcontainer/python/3.13.16/python.3.13.16.nupkg'
$script:PythonPackageHash = '95fad176338f1d6a799e45946488063f4aa61764e6cff2f132ddeffbf7a04c34'

function Get-LoomStrokePythonPackage {
    param([string]$Destination)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $request = [Net.HttpWebRequest]::Create($script:PythonPackageUrl)
    $request.Timeout = 120000
    $request.ReadWriteTimeout = 120000
    $response = $request.GetResponse()
    try {
        $inputStream = $response.GetResponseStream()
        $outputStream = [IO.File]::Create($Destination)
        try {
            $buffer = New-Object byte[] 65536
            $total = 0
            while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $total += $read
                if ($total -gt 52428800) { throw 'Python package exceeds the download size limit.' }
                $outputStream.Write($buffer, 0, $read)
            }
        } finally { $outputStream.Dispose(); $inputStream.Dispose() }
    } finally { $response.Dispose() }
}

function Test-LoomStrokePrivatePython {
    param([string]$Python)
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { return $false }
    & $Python -I -c "import ensurepip, struct, sys, venv; sys.exit(0 if sys.version_info[:3] == (3, 13, 16) and struct.calcsize('P') == 8 else 1)" > $null 2>&1
    return $LASTEXITCODE -eq 0
}

function Install-LoomStrokePython {
    param([string]$Root)
    $ErrorActionPreference = 'Stop'
    if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $runtime = Join-Path $rootPath ('python-' + $script:PythonVersion)
    $python = Join-Path $runtime 'tools\python.exe'
    $marker = Join-Path $runtime 'loomstroke-package.sha256'
    if (Test-Path -LiteralPath $runtime) {
        if ((Test-Path -LiteralPath $marker -PathType Leaf) -and
            (Get-Content -LiteralPath $marker -Raw).Trim() -eq $script:PythonPackageHash -and
            (Test-LoomStrokePrivatePython $python)) {
            Write-Host 'Reusing the private LoomStroke Python runtime.'
            return
        }
        throw 'The existing private runtime is incomplete. It was preserved; rename it and retry.'
    }
    [IO.Directory]::CreateDirectory($rootPath) > $null
    $stage = Join-Path $rootPath ('stage-' + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($stage) > $null
    try {
        $package = Join-Path $stage 'python.nupkg'
        Write-Host ('Downloading official CPython ' + $script:PythonVersion + ' (64-bit)...')
        Get-LoomStrokePythonPackage $package
        $sha = [Security.Cryptography.SHA256]::Create()
        $packageStream = [IO.File]::OpenRead($package)
        try {
            $hash = [BitConverter]::ToString($sha.ComputeHash($packageStream)).Replace('-', '').ToLowerInvariant()
        } finally { $packageStream.Dispose(); $sha.Dispose() }
        if ($hash -ne $script:PythonPackageHash) {
            throw 'Python download SHA-256 verification failed. Nothing was executed.'
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $payload = Join-Path $stage 'payload'
        [IO.Directory]::CreateDirectory($payload) > $null
        $zip = [IO.Compression.ZipFile]::OpenRead($package)
        try {
            foreach ($entry in $zip.Entries) {
                $target = [IO.Path]::GetFullPath((Join-Path $payload $entry.FullName))
                if (-not $target.StartsWith($payload + '\', [StringComparison]::OrdinalIgnoreCase)) {
                    throw 'Unsafe entry in the Python package.'
                }
            }
        } finally { $zip.Dispose() }
        [IO.Compression.ZipFile]::ExtractToDirectory($package, $payload)
        if (-not (Test-LoomStrokePrivatePython (Join-Path $payload 'tools\python.exe'))) {
            throw 'The downloaded Python could not run with venv and ensurepip support.'
        }
        [IO.File]::WriteAllText((Join-Path $payload 'loomstroke-package.sha256'), $script:PythonPackageHash)
        # Both move paths are generated strictly under the selected runtime root.
        if (-not ([IO.Path]::GetFullPath($payload)).StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not ([IO.Path]::GetFullPath($runtime)).StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Private runtime paths escaped their destination.'
        }
        Move-Item -LiteralPath $payload -Destination $runtime
        Write-Host 'Private Python is ready. System Python and PATH were not changed.'
    } finally {
        $stagePath = [IO.Path]::GetFullPath($stage)
        if ($stagePath.StartsWith($rootPath + '\stage-', [StringComparison]::OrdinalIgnoreCase) -and
            (Test-Path -LiteralPath $stagePath)) {
            Remove-Item -LiteralPath $stagePath -Recurse -Force
        }
    }
}

# Dot-sourcing exposes the functions for isolated, offline regression tests.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Install-LoomStrokePython -Root $RuntimeRoot
        exit 0
    } catch {
        Write-Error ('Python setup failed: ' + $_.Exception.Message)
        exit 1
    }
}
