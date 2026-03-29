param(
    [string]$Port,
    [int]$BaudRate = 115200,
    [switch]$ListPorts,
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$IgnoreFile = Join-Path $ProjectRoot ".deployignore"

function Get-DeployIgnorePatterns {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return @()
    }

    return Get-Content $Path |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
}

function Test-IgnoredPath {
    param(
        [string]$RelativePath,
        [string[]]$Patterns
    )

    $normalized = $RelativePath -replace "\\", "/"
    foreach ($pattern in $Patterns) {
        $normalizedPattern = $pattern -replace "\\", "/"
        if ($normalized -like $normalizedPattern) {
            return $true
        }
    }
    return $false
}

function Get-DeployFiles {
    param(
        [string]$Root,
        [string[]]$IgnorePatterns
    )

    $items = Get-ChildItem -Path $Root -Recurse -File
    $files = foreach ($item in $items) {
        $relative = $item.FullName.Substring($Root.Length).TrimStart('\')
        if (-not (Test-IgnoredPath -RelativePath $relative -Patterns $IgnorePatterns)) {
            [PSCustomObject]@{
                FullName = $item.FullName
                RelativePath = $relative -replace "\\", "/"
            }
        }
    }

    return $files | Sort-Object RelativePath
}

function Get-SerialPorts {
    return [System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object
}

function Read-AvailableText {
    param(
        [System.IO.Ports.SerialPort]$SerialPort,
        [int]$DelayMs = 120
    )

    Start-Sleep -Milliseconds $DelayMs
    $builder = New-Object System.Text.StringBuilder
    while ($SerialPort.BytesToRead -gt 0) {
        [void]$builder.Append($SerialPort.ReadExisting())
        Start-Sleep -Milliseconds 30
    }
    return $builder.ToString()
}

function Read-Until {
    param(
        [System.IO.Ports.SerialPort]$SerialPort,
        [string]$Needle,
        [int]$TimeoutMs = 5000
    )

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $builder = New-Object System.Text.StringBuilder
    while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
        if ($SerialPort.BytesToRead -gt 0) {
            [void]$builder.Append($SerialPort.ReadExisting())
            $text = $builder.ToString()
            if ($text.Contains($Needle)) {
                return $text
            }
        } else {
            Start-Sleep -Milliseconds 25
        }
    }

    throw "Timed out waiting for '$Needle'. Received: $($builder.ToString())"
}

function Read-RawResponse {
    param(
        [System.IO.Ports.SerialPort]$SerialPort,
        [int]$TimeoutMs = 8000
    )

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $bytes = New-Object System.Collections.Generic.List[byte]
    $terminators = 0

    while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
        if ($SerialPort.BytesToRead -gt 0) {
            $value = $SerialPort.ReadByte()
            if ($value -lt 0) {
                continue
            }

            $byte = [byte]$value
            $bytes.Add($byte)
            if ($byte -eq 4) {
                $terminators += 1
                if ($terminators -ge 2) {
                    break
                }
            }
        } else {
            Start-Sleep -Milliseconds 20
        }
    }

    if ($terminators -lt 2) {
        throw "Timed out waiting for raw REPL response terminators."
    }

    if ($bytes.Count -lt 4) {
        throw "Unexpected raw REPL response."
    }

    $allBytes = $bytes.ToArray()
    $okIndex = -1
    for ($i = 0; $i -lt $allBytes.Length - 1; $i++) {
        if ($allBytes[$i] -eq 79 -and $allBytes[$i + 1] -eq 75) {
            $okIndex = $i
            break
        }
    }

    if ($okIndex -lt 0) {
        $prefix = [System.Text.Encoding]::UTF8.GetString($allBytes)
        throw "Raw REPL did not acknowledge command. Response: $prefix"
    }

    $firstTerminator = [Array]::IndexOf($allBytes, [byte]4, $okIndex + 2)
    if ($firstTerminator -lt 0) {
        throw "Malformed raw REPL response."
    }
    $stdoutStart = $okIndex + 2
    $stdoutLength = $firstTerminator - $stdoutStart
    if ($stdoutLength -lt 0) {
        throw "Malformed raw REPL response."
    }
    $stdoutBytes = $bytes.GetRange($stdoutStart, $stdoutLength).ToArray()
    $stderrStart = $firstTerminator + 1
    $stderrLength = $bytes.Count - $stderrStart - 1
    $stderrBytes = @()
    if ($stderrLength -gt 0) {
        $stderrBytes = $bytes.GetRange($stderrStart, $stderrLength).ToArray()
    }

    return [PSCustomObject]@{
        StdOut = [System.Text.Encoding]::UTF8.GetString($stdoutBytes)
        StdErr = [System.Text.Encoding]::UTF8.GetString($stderrBytes)
    }
}

function Invoke-RawRepl {
    param(
        [System.IO.Ports.SerialPort]$SerialPort,
        [string]$Code,
        [int]$TimeoutMs = 8000
    )

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Code)
    $SerialPort.Write($bytes, 0, $bytes.Length)
    $SerialPort.Write([byte[]](4), 0, 1)
    $response = Read-RawResponse -SerialPort $SerialPort -TimeoutMs $TimeoutMs

    if ($response.StdErr) {
        throw "Remote error: $($response.StdErr.Trim())"
    }

    return $response.StdOut.Trim()
}

function Enter-RawRepl {
    param([System.IO.Ports.SerialPort]$SerialPort)

    $SerialPort.DiscardInBuffer()
    $SerialPort.DiscardOutBuffer()
    $SerialPort.Write("`r`n")
    $SerialPort.Write([byte[]](3, 3), 0, 2)
    Start-Sleep -Milliseconds 150
    $SerialPort.Write([byte[]](1), 0, 1)
    [void](Read-Until -SerialPort $SerialPort -Needle "raw REPL; CTRL-B to exit")
}

function Exit-RawRepl {
    param([System.IO.Ports.SerialPort]$SerialPort)

    $SerialPort.Write([byte[]](2), 0, 1)
    Start-Sleep -Milliseconds 100
}

function ConvertTo-PythonBytesLiteral {
    param([byte[]]$Data)

    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append("b'")
    foreach ($byte in $Data) {
        [void]$builder.AppendFormat("\x{0:x2}", $byte)
    }
    [void]$builder.Append("'")
    return $builder.ToString()
}

function Split-ByteArray {
    param(
        [byte[]]$Data,
        [int]$ChunkSize
    )

    $chunks = @()
    for ($offset = 0; $offset -lt $Data.Length; $offset += $ChunkSize) {
        $length = [Math]::Min($ChunkSize, $Data.Length - $offset)
        $chunk = New-Object byte[] $length
        [Array]::Copy($Data, $offset, $chunk, 0, $length)
        $chunks += ,$chunk
    }
    return $chunks
}

function Get-RemoteDirectories {
    param([object[]]$Files)

    $directories = New-Object System.Collections.Generic.HashSet[string]
    foreach ($file in $Files) {
        $parts = $file.RelativePath.Split("/")
        if ($parts.Length -le 1) {
            continue
        }

        $current = ""
        for ($i = 0; $i -lt $parts.Length - 1; $i++) {
            if ($current) {
                $current = "$current/$($parts[$i])"
            } else {
                $current = $parts[$i]
            }
            [void]$directories.Add($current)
        }
    }

    return @($directories) | Sort-Object
}

function Remove-RemotePath {
    param(
        [System.IO.Ports.SerialPort]$SerialPort,
        [string]$Path
    )

    $escaped = $Path.Replace("\", "/")
    $code = @"
import os
def _rm(path):
    try:
        mode = os.stat(path)[0]
    except OSError:
        return
    if mode & 0x4000:
        try:
            names = os.listdir(path)
        except OSError:
            names = []
        for name in names:
            _rm(path + "/" + name)
        try:
            os.rmdir(path)
        except OSError:
            pass
    else:
        try:
            os.remove(path)
        except OSError:
            pass
_rm('$escaped')
"@
    [void](Invoke-RawRepl -SerialPort $SerialPort -Code $code -TimeoutMs 15000)
}

function Ensure-RemoteDirectories {
    param(
        [System.IO.Ports.SerialPort]$SerialPort,
        [string[]]$Directories
    )

    foreach ($directory in $Directories) {
        $code = @"
import os
try:
    os.mkdir('$directory')
except OSError:
    pass
"@
        [void](Invoke-RawRepl -SerialPort $SerialPort -Code $code)
    }
}

function Write-RemoteFile {
    param(
        [System.IO.Ports.SerialPort]$SerialPort,
        [string]$LocalPath,
        [string]$RemotePath
    )

    $data = [System.IO.File]::ReadAllBytes($LocalPath)
    $chunks = Split-ByteArray -Data $data -ChunkSize 192
    $first = $true

    foreach ($chunk in $chunks) {
        $mode = if ($first) { "wb" } else { "ab" }
        $literal = ConvertTo-PythonBytesLiteral -Data $chunk
        $code = "with open('$RemotePath','$mode') as f:`n    f.write($literal)"
        [void](Invoke-RawRepl -SerialPort $SerialPort -Code $code -TimeoutMs 12000)
        $first = $false
    }

    if ($data.Length -eq 0) {
        [void](Invoke-RawRepl -SerialPort $SerialPort -Code "open('$RemotePath','wb').close()")
    }
}

if ($ListPorts) {
    $ports = Get-SerialPorts
    if (-not $ports) {
        Write-Host "No serial ports found."
        exit 0
    }

    $ports | ForEach-Object { Write-Host $_ }
    exit 0
}

if (-not $Port) {
    $ports = Get-SerialPorts
    if ($ports.Count -eq 1) {
        $Port = $ports[0]
    } else {
        throw "Specify -Port COMx. Available ports: $($ports -join ', ')"
    }
}

$ignorePatterns = Get-DeployIgnorePatterns -Path $IgnoreFile
$files = Get-DeployFiles -Root $ProjectRoot -IgnorePatterns $ignorePatterns
if (-not $files) {
    throw "No deployable files found."
}

$directories = Get-RemoteDirectories -Files $files

$serialPort = New-Object System.IO.Ports.SerialPort $Port, $BaudRate, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One)
$serialPort.Handshake = [System.IO.Ports.Handshake]::None
$serialPort.NewLine = "`r`n"
$serialPort.ReadTimeout = 200
$serialPort.WriteTimeout = 5000

try {
    $serialPort.Open()
    Enter-RawRepl -SerialPort $serialPort

    if ($Clean) {
        $topLevel = $files |
            ForEach-Object { $_.RelativePath.Split("/")[0] } |
            Select-Object -Unique
        foreach ($path in $topLevel) {
            Write-Host "Removing remote $path"
            Remove-RemotePath -SerialPort $serialPort -Path $path
        }
    }

    Ensure-RemoteDirectories -SerialPort $serialPort -Directories $directories

    foreach ($file in $files) {
        Write-Host "Uploading $($file.RelativePath)"
        Write-RemoteFile -SerialPort $serialPort -LocalPath $file.FullName -RemotePath $file.RelativePath
    }

    Write-Host "Soft rebooting board"
    $serialPort.Write([byte[]](4), 0, 1)
    Start-Sleep -Milliseconds 500
    Exit-RawRepl -SerialPort $serialPort
    $bootLog = Read-AvailableText -SerialPort $serialPort -DelayMs 500
    if ($bootLog) {
        Write-Host ""
        Write-Host "Board output:"
        Write-Host $bootLog.TrimEnd()
    }
} finally {
    if ($serialPort.IsOpen) {
        $serialPort.Close()
    }
    $serialPort.Dispose()
}
