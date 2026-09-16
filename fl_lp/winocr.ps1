# winocr.ps1 - OCR image files with Windows' built-in engine (Windows.Media.Ocr).
# Prints ONE JSON object on stdout: {"<path>": "<recognized text, lines joined by \n>" | null}.
# Windows PowerShell 5.1 only (it carries the WinRT projection); nothing to install. Used by
# fl_lp/broward_pin.py to read scanned recorded mortgages, which have no text layer.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Paths)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $null = $task.Wait(-1)
    $task.Result
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {
    [Console]::Error.WriteLine('winocr: no OCR recognizer language is installed')
    exit 3
}
$out = [ordered]@{}
foreach ($p in $Paths) {
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($p)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        try {
            $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $out[$p] = (($result.Lines | ForEach-Object { $_.Text }) -join "`n")
        } finally {
            $stream.Dispose()
        }
    } catch {
        [Console]::Error.WriteLine("winocr: $p : $($_.Exception.Message)")
        $out[$p] = $null
    }
}
$out | ConvertTo-Json -Compress
