function Invoke-WithClearedPythonPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action
    )

    $PreviousPythonPathForImportSmokes = $env:PYTHONPATH
    $PythonPathWasPresent = Test-Path Env:PYTHONPATH
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    try {
        & $Action
    } finally {
        if ($PythonPathWasPresent) {
            $env:PYTHONPATH = $PreviousPythonPathForImportSmokes
        } else {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        }
    }
}
