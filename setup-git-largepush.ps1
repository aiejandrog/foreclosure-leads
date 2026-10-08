# setup-git-largepush.ps1 — run ONCE per machine (laptop, desktop, any fresh clone).
#
# WHY: the live board (docs/index.html) is a ~19 MB file. Pushing it over HTTPS with git's
# default send buffer + HTTP/2 intermittently dies with "HTTP 408 / send-pack: unexpected
# disconnect" (a known GitHub large-push failure, NOT an MTU problem). The fix is a bigger
# postBuffer and forcing HTTP/1.1.
#
# WHY A SCRIPT AND NOT A COMMITTED CONFIG: git deliberately does NOT read http.* settings from a
# tracked file on clone (security — that is why .git/config is never committed and .gitattributes
# cannot set http.postBuffer). So "machine-independent" means this one-shot, version-controlled
# script — each machine runs it once and every repo for that user inherits the fix via --global.
#
# Idempotent: safe to run repeatedly.

git config --global http.postBuffer 524288000   # 500 MB send buffer
git config --global http.version    HTTP/1.1     # avoid the HTTP/2 large-push 408

Write-Host "git large-push fix applied (global):"
Write-Host ("  http.postBuffer = " + (git config --global --get http.postBuffer))
Write-Host ("  http.version    = " + (git config --global --get http.version))
