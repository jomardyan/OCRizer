param(
    [string]$Python = 'python',
    [string]$TesseractDirectory = '',
    [switch]$Clean,
    [switch]$SkipBuild,
    [switch]$SkipSmokeTest
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    if (-not $SkipBuild) {
        $taskBuildArguments = @('-m', 'PyInstaller', '--noconfirm', 'packaging/ocrizer.spec')
        if ($Clean) { $taskBuildArguments += '--clean' }
        & $Python @taskBuildArguments
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    }
    if (-not $TesseractDirectory) {
        $taskCandidates = @(
            (Join-Path $env:ProgramFiles 'Tesseract-OCR'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Tesseract-OCR')
        )
        foreach ($taskCandidate in $taskCandidates) {
            if (Test-Path -LiteralPath (Join-Path $taskCandidate 'tesseract.exe')) {
                $TesseractDirectory = $taskCandidate
                break
            }
        }
    }
    if (-not $TesseractDirectory -or -not (Test-Path -LiteralPath (Join-Path $TesseractDirectory 'tesseract.exe'))) {
        throw 'Install Tesseract, or pass -TesseractDirectory with its complete installation folder.'
    }
    $taskOutput = Join-Path $taskRoot 'dist\OCRizer'
    $taskTesseract = Join-Path $taskOutput 'Tesseract-OCR'
    New-Item -ItemType Directory -Force -Path $taskTesseract | Out-Null
    Get-ChildItem -LiteralPath $TesseractDirectory -Force | Copy-Item -Destination $taskTesseract -Recurse -Force
    $taskTessdata = Join-Path $taskTesseract 'tessdata'
    New-Item -ItemType Directory -Force -Path $taskTessdata | Out-Null
    foreach ($taskLanguage in @('eng', 'pol', 'osd')) {
        $taskData = Join-Path $taskTessdata "$taskLanguage.traineddata"
        if (-not (Test-Path -LiteralPath $taskData)) {
            Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/$taskLanguage.traineddata" -OutFile $taskData
        }
    }
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/LICENSE' -OutFile (Join-Path $taskTessdata 'LICENSE')
    & $Python packaging/collect_licenses.py $taskOutput
    if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
    Copy-Item -LiteralPath README.md -Destination $taskOutput
    Copy-Item -LiteralPath LICENSE -Destination $taskOutput
    $taskVersion = & $Python -c 'from ocrizer import __version__; print(__version__)'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot determine version.' }
    if (-not $SkipSmokeTest) {
        & $Python packaging/smoke_test.py --cli (Join-Path $taskOutput 'pdfocr.exe') --gui (Join-Path $taskOutput 'OCRizer.exe')
        if ($LASTEXITCODE -ne 0) { throw 'Frozen application smoke test failed.' }
    }
    $taskArchive = Join-Path $taskRoot "dist\OCRizer-$taskVersion-windows-x86_64.zip"
    Compress-Archive -LiteralPath $taskOutput -DestinationPath $taskArchive -Force
    Write-Output $taskArchive
} finally {
    Pop-Location
}
