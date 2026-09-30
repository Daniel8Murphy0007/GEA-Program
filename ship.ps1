<#
.SYNOPSIS
  ship.ps1 - the GEA-Program ship: gate, commit, tag, push, verify. One screen.

.DESCRIPTION
  .\ship.ps1                    ship the version in pyproject.toml
  .\ship.ps1 -Bump 0.2.0        set the version in pyproject.toml and gea/__init__.py first
  .\ship.ps1 -DryRun            run every check, change nothing
  .\ship.ps1 -NoPush            commit and tag locally, do not push

  Rules, in order, each one stopping the ship if it fails:
    1. clean git state: stale .git/index.lock and COMMIT_EDITMSG removed; not behind origin; LICENSE present
    2. version in pyproject.toml == gea.__version__; tag v<version> exists nowhere (local or remote)
    3. SHIP_LOG.md chain: every version already logged has a tag (a ship is not a ship until its tag exists)
    4. gate: python -m gea accept exits 0; tools/standalone_check.py exits 0 (self-contained: imports, text, metadata)
    5. SHIP_MESSAGE.txt exists and its first line starts with the tag; CHANGELOG.md has a section for the tag
    6. git add -A; commit -F SHIP_MESSAGE.txt; HEAD must advance
    7. tag -a; tag^{commit} must equal HEAD
    8. push branch and tag; the REMOTE tag must be seen before SHIPPED is printed
    9. SHIP_LOG.md gets its line (committed with the next ship, as history)
#>
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
param(
    [string]$Bump = "",
    [switch]$DryRun,
    [switch]$NoPush
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Fail($msg) { Write-Host "SHIP STOPPED: $msg" -ForegroundColor Red; exit 1 }
function Step($msg) { Write-Host "== $msg" -ForegroundColor Cyan }

# 1. git state ------------------------------------------------------------------------------------
Step "git state"
if (-not (Test-Path ".git")) { Fail "not a git repository: $root" }
foreach ($f in @(".git\index.lock", ".git\COMMIT_EDITMSG")) {
    if (Test-Path $f) { Remove-Item $f -Force; Write-Host "   removed stale $f" }
}
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$remote = (git remote get-url origin).Trim()
Write-Host "   branch $branch -> $remote"
git fetch origin 2>$null | Out-Null
$behind = (git rev-list --count "HEAD..origin/$branch" 2>$null)
if ($behind -and [int]$behind -gt 0) { Fail "local $branch is $behind commit(s) behind origin/$branch - run: git pull   (a change made on GitHub, such as the LICENSE, is not here yet)" }
if (-not (Test-Path LICENSE)) { Fail "LICENSE missing - the repository licence (MPL-2.0) must be present before a ship" }

# 2. version --------------------------------------------------------------------------------------
Step "version"
$py = Get-Content pyproject.toml -Raw
$init = Get-Content gea\__init__.py -Raw
if ($Bump -ne "") {
    if ($Bump -notmatch '^\d+\.\d+\.\d+$') { Fail "-Bump must be MAJOR.MINOR.PATCH, got '$Bump'" }
    $py = [regex]::Replace($py, '(?m)^version = "[^"]+"', "version = `"$Bump`"", 1)
    $init = [regex]::Replace($init, '(?m)^__version__ = "[^"]+"', "__version__ = `"$Bump`"", 1)
    if (-not $DryRun) {
        [IO.File]::WriteAllText("$root\pyproject.toml", $py)
        [IO.File]::WriteAllText("$root\gea\__init__.py", $init)
    }
    Write-Host "   bumped to $Bump"
}
$version = ([regex]::Match($py, '(?m)^version = "([^"]+)"')).Groups[1].Value
$initv   = ([regex]::Match($init, '(?m)^__version__ = "([^"]+)"')).Groups[1].Value
if ($version -eq "") { Fail "no version in pyproject.toml" }
if ($version -ne $initv) { Fail "pyproject.toml version $version != gea.__version__ $initv (use -Bump to set both)" }
$tag = "v$version"
if ("$(git tag -l $tag)".Trim() -eq $tag) { Fail "tag $tag already exists locally" }
$remoteTag = git ls-remote --tags origin "refs/tags/$tag" 2>$null
if ($remoteTag) { Fail "tag $tag already exists on origin" }
Write-Host "   $tag is free"

# 3. ship-log chain ----------------------------------------------------------------------------------
Step "ship-log chain"
if (Test-Path SHIP_LOG.md) {
    $logged = Select-String -Path SHIP_LOG.md -Pattern '^\| (v\d+\.\d+\.\d+) \|' | ForEach-Object { $_.Matches[0].Groups[1].Value }
    foreach ($v in $logged) {
        if ("$(git tag -l $v)".Trim() -ne $v) { Fail "SHIP_LOG.md lists $v but no such tag exists - the chain is broken; fix the log or the tag before shipping" }
    }
    Write-Host "   $($logged.Count) logged ships, every one tagged"
} else {
    Write-Host "   no SHIP_LOG.md yet (first ship)"
}

# 4. gate ----------------------------------------------------------------------------------------------
Step "gate: python -m gea accept"
python -m gea accept
if ($LASTEXITCODE -ne 0) { Fail "acceptance suite red" }
Step "standalone check"
python tools\standalone_check.py --quiet
if ($LASTEXITCODE -ne 0) { Fail "standalone check red - a tracked file names another program (run python tools\standalone_check.py for the lines)" }

# 5. ship message ----------------------------------------------------------------------------------------
Step "ship message"
if (-not (Test-Path SHIP_MESSAGE.txt)) { Fail "SHIP_MESSAGE.txt missing - write the commit message, first line starting with $tag" }
$subject = (Get-Content SHIP_MESSAGE.txt -TotalCount 1).Trim()
if (-not $subject.StartsWith($tag)) { Fail "SHIP_MESSAGE.txt first line must start with $tag, got: $subject" }
Write-Host "   $subject"
if (-not (Test-Path CHANGELOG.md)) { Fail "CHANGELOG.md missing" }
$section = Select-String -Path CHANGELOG.md -Pattern ("^## \[" + [regex]::Escape($tag) + "\]") -Quiet
if (-not $section) { Fail "CHANGELOG.md has no section headed ## [$tag] - write the release notes before shipping" }
Write-Host "   CHANGELOG.md has its $tag section"

if ($DryRun) { Write-Host "DRY RUN: all checks passed; nothing changed." -ForegroundColor Green; exit 0 }

# 6. commit -------------------------------------------------------------------------------------------------
Step "commit"
$before = (git rev-parse HEAD 2>$null)
git add -A
git commit -F SHIP_MESSAGE.txt | Out-Null
$head = (git rev-parse HEAD).Trim()
if ($head -eq $before) { Fail "commit did not advance HEAD (nothing to commit?)" }
Write-Host "   $head"

# 7. tag -----------------------------------------------------------------------------------------------------
Step "tag $tag"
git tag -a $tag -m $subject
$tagCommit = (git rev-parse "$tag^{commit}").Trim()
if ($tagCommit -ne $head) { Fail "tag $tag points at $tagCommit, HEAD is $head" }
Write-Host "   $tag -> $head"

# 8. push and verify -----------------------------------------------------------------------------------------
if ($NoPush) { Write-Host "NOPUSH: committed and tagged locally; push with: git push origin $branch; git push origin $tag" -ForegroundColor Yellow; exit 0 }
Step "push"
git push origin $branch
if ($LASTEXITCODE -ne 0) { Fail "push of $branch failed (a token without the 'workflow' scope cannot push .github/workflows changes)" }
git push origin $tag
if ($LASTEXITCODE -ne 0) { Fail "push of $tag failed" }
$seen = git ls-remote --tags origin "refs/tags/$tag"
if (-not $seen) { Fail "remote tag $tag not visible after push" }

# 9. ship log --------------------------------------------------------------------------------------------------
if (-not (Test-Path SHIP_LOG.md)) {
    "# Ship log`n`n| version | date (UTC) | commit | subject |`n|---|---|---|---|" | Set-Content SHIP_LOG.md
}
$date = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm")
Add-Content SHIP_LOG.md "| $tag | $date | $($head.Substring(0,10)) | $subject |"
Write-Host ""
Write-Host "SHIPPED $tag -> $remote ($($head.Substring(0,10)))" -ForegroundColor Green
Write-Host "SHIP_LOG.md updated; it rides in the next commit."
