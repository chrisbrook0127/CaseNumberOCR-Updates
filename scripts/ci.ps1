param(
    [Parameter(Mandatory)][ValidateSet('Setup','Test','Package','VerifyPackage')][string]$Phase,
    [Parameter(Mandatory)][ValidateSet('Legacy','Stable','Candidate')][string]$Target,
    [Parameter(Mandatory)][string]$Root
)
$ErrorActionPreference = 'Stop'
$rootPath = (Resolve-Path -LiteralPath $Root).Path
$verify = Join-Path $PSScriptRoot 'ci_verify.py'
function Invoke-Checked {
    param([string]$Command,[string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed ($LASTEXITCODE)" }
}
Push-Location -LiteralPath $rootPath
try {
    $env:MFCASE_SKIP_STARTUP_MAINTENANCE = '1'
    switch ($Phase) {
        Setup {
            Invoke-Checked python @('-c','import sys; assert sys.version_info[:2] == (3,11)')
            Invoke-Checked python @('-m','pip','install','numpy','pillow','pymupdf<2.0','opencv-python-headless')
            if ($Target -eq 'Stable') {
                Invoke-Checked python @('tools/verify_v603.py','prepare')
            } elseif ($Target -eq 'Candidate') {
                New-Item -ItemType Directory -Path baseline -Force | Out-Null
                Invoke-Checked gh @('release','download','v6.0.3','--repo','chrisbrook0127/CaseNumberOCR-Updates','--pattern','MFCase_Number_OCR_V6.0.3_Full_Online_Install.zip','--dir','baseline')
                Invoke-Checked python @('tools/build_v604_test.py','assemble')
            }
        }
        Test {
            if ($Target -eq 'Legacy') {
                Invoke-Checked python @('-m','compileall','-q','app')
                Invoke-Checked python @($verify,'tests','tests','test_*.py','app')
            } else {
                if ($Target -eq 'Stable') {
                    $matches = @(Get-ChildItem -Path stage -Filter case_number_ocr.py -Recurse)
                    if ($matches.Count -ne 1) { throw 'Expected exactly one staged stable app' }
                    $appPath = $matches[0].Directory.FullName
                } else {
                    $appPath = (Get-Content -LiteralPath stage_path.txt -Raw).Trim()
                }
                Invoke-Checked python @($verify,'tests',(Join-Path $appPath 'tests'),'test_release_*.py',$appPath)
            }
        }
        Package {
            if ($Target -eq 'Stable') {
                Invoke-Checked python @('tools/verify_v603.py','updater')
            } elseif ($Target -eq 'Candidate') {
                Invoke-Checked python @('tools/build_v604_test.py','package')
            } else { throw 'Legacy packaging intentionally unsupported; not a current release' }
        }
        VerifyPackage {
            if ($Target -eq 'Legacy') { throw 'Legacy has no current release artifact' }
            Invoke-Checked python @($verify,'packages',$Target,$rootPath)
        }
    }
} finally { Pop-Location }
