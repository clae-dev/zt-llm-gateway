# 로컬 Ollama에 연결해 게이트웨이를 띄운다.
#   .\run-ollama.ps1                                 규칙만
#   .\run-ollama.ps1 -Kanana -Secrets HALLA-7741     카카오 탐지 모델 + 응답 기밀 검사
#   .\run-ollama.ps1 -Kanana -Demo                   시연용: 시나리오(S1 사내문서, S2 고객기록)의 비밀 값을 기밀로 등록
param([switch]$Kanana, [switch]$Demo, [string]$Secrets = "")
$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
try { Invoke-WebRequest http://127.0.0.1:11434/api/tags -TimeoutSec 3 | Out-Null }
catch {
    Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden
    foreach ($i in 1..15) { try { Invoke-WebRequest http://127.0.0.1:11434/api/tags -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 2 } }
}

$env:UPSTREAM_BASE_URL = "http://127.0.0.1:11434/v1"
$env:UPSTREAM_TIMEOUT_S = "180"   # 첫 요청은 모델을 GPU에 올리느라 오래 걸린다
if ($Kanana) { $env:GUARD_MODEL = "kakaocorp/kanana-safeguard-prompt-2.1b" }   # transformers 필요, 미리 내려받아 둘 것
if ($Secrets) { $env:SECRET_TERMS = $Secrets }
if ($Demo) { $env:DEMO = "1" }
Set-Location $PSScriptRoot
python -m uvicorn app.main:app --port 8787
