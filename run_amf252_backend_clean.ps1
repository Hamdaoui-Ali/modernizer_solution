# =====================================================================
# AI Migration Control Tower — Minimal AMF-252 Backend Launcher
# Windows PowerShell 5.1
#
# Flow:
#   1. Validate repository state and migration state
#   2. Configure AMF-252 environment
#   3. Make tiny proposer + reviewer connectivity smoke calls
#   4. Print MODEL OK or MODEL WARNING per role
#   5. Start backend directly in foreground
#
# No Invoke-RestMethod for model smoke.
# No background jobs.
# No broad smoke matrix.
#
# The smoke uses temporary UTF-8 no-BOM request/response files to avoid
# Windows PowerShell 5.1 + native curl JSON quoting/BOM problems.
# =====================================================================

param(
    [switch]$PreflightOnly,
    [switch]$SkipModelSmokeTest,
    [switch]$SkipReviewerSmokeTest,
    [switch]$RequireSmokeSuccess,
    [string]$ProposerModelOverride = "",
    [string]$MainModelOverride = "",
    [string]$ReviewerModelOverride = "",
    [string]$FallbackModelOverride = "",
    [string]$ReviewerResponseFormatOverride = "",
    [int]$MaxRepairAttemptsOverride = 0
)

$ErrorActionPreference = "Stop"


# ---------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------

$RepoRoot = "C:\Users\abdelilah.mortaki\Desktop\modernizer-solution"



$MigrationsDir = Join-Path `
    $RepoRoot `
    "migration_factory\control_tower\infrastructure\sqlite\migrations"

$ExpectedRepairMigrationName = `
    "0061_repair_assistant_failure_diagnostics.sql"

$RequiredRepairMigrationNames = @(
    "0052_v2_repair_proposals_rule_id_risk.sql",
    "0053_v2_repair_lineage_claims.sql",
    "0054_v2_repair_final_diff_source.sql",
    "0055_target_version_validation.sql",
    "0058_repair_assistant_processing_leases.sql",
    "0059_repair_assistant_scoped_idempotency.sql",
    "0060_v2_llm_invocation_runtime_metadata.sql",
    "0061_repair_assistant_failure_diagnostics.sql"
)

$OldConflictingRepairMigrationName = `
    "0050_v2_repair_proposals_rule_id_risk.sql"


if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
    throw "Repo root not found: $RepoRoot"
}

Set-Location $RepoRoot

$env:PYTHONPATH = "."


# ---------------------------------------------------------------------
# Local toolchain
# ---------------------------------------------------------------------

$Java11Home = "C:\Users\abdelilah.mortaki\.jdks\temurin-11.0.31"
$Java17Home = "C:\Users\abdelilah.mortaki\.jdks\ms-17.0.19"
$Java21Home = "C:\Program Files\Eclipse Adoptium\Temurin-21"

$MavenHome = "C:\Tools\apache-maven-3.9.15"

$GitCmd = "C:\Users\abdelilah.mortaki\AppData\Local\Programs\Git\cmd"
$GitBin = "C:\Users\abdelilah.mortaki\AppData\Local\Programs\Git\bin"


# ---------------------------------------------------------------------
# Azure / models
# ---------------------------------------------------------------------

$AzureOpenAIEndpoint = `
    "https://abdelilahmortaki-9971-resource.openai.azure.com/openai/v1"

$ProposerModel = if ($ProposerModelOverride) { $ProposerModelOverride } elseif ($env:AMF252_PROPOSER_MODEL) { $env:AMF252_PROPOSER_MODEL } else { "gpt-5-mini" }
$MainModel = if ($MainModelOverride) { $MainModelOverride } elseif ($env:AMF252_MAIN_MODEL) { $env:AMF252_MAIN_MODEL } else { $ProposerModel }
$ReviewerModel = if ($ReviewerModelOverride) { $ReviewerModelOverride } elseif ($env:AMF252_REVIEWER_MODEL) { $env:AMF252_REVIEWER_MODEL } else { "Llama-3.3-70B-Instruct" }
$FallbackModel = if ($FallbackModelOverride) { $FallbackModelOverride } elseif ($env:AMF252_FALLBACK_MODEL) { $env:AMF252_FALLBACK_MODEL } else { "gpt-5-mini" }

$MaxRepairAttempts = if ($MaxRepairAttemptsOverride -gt 0) {
    $MaxRepairAttemptsOverride
}
elseif ($env:AMF252_MAX_REPAIR_ATTEMPTS) {
    try {
        [int]$env:AMF252_MAX_REPAIR_ATTEMPTS
    }
    catch {
        throw "AMF252_MAX_REPAIR_ATTEMPTS must be an integer."
    }
}
else {
    3
}

if ($MaxRepairAttempts -lt 1) {
    throw "AMF252 max repair attempts must be at least 1."
}


# ---------------------------------------------------------------------
# AMF-252 model budgets
#
# Input:
#   50K configured input tokens.
#
# Output:
#   GPT-5 mini proposer  = 20,000
#   GPT-5 mini main      = 20,000
#   GPT-5 mini assistant = 20,000
#   Llama 3.3 reviewer   = 8,192
#   GPT-5 mini fallback  = 20,000
#
# Llama-3.3-70B-Instruct is configured separately because its documented
# maximum output is 8,192 tokens.
# ---------------------------------------------------------------------

$RoleMaxInputTokens = "40000"

$DefaultMaxOutputTokens = "20000"

$ProposerMaxOutputTokens = "20000"
$MainMaxOutputTokens = "20000"
$AssistantMaxOutputTokens = "20000"
$ReviewerMaxOutputTokens = "8192"
$FallbackMaxOutputTokens = "20000"

$RuntimeReasoningEffort = "medium"
$RuntimeResponseFormat = "json_schema"

# The current AMF-252 reviewer call is schema-bound by the backend contract.
# Use json_schema for capable reviewers, while preferring json_object for the
# default text-only Llama reviewer.
#
# IMPORTANT: Microsoft currently documents Llama-3.3-70B-Instruct as a
# text-only response-format model. If the deployed endpoint rejects JSON
# Schema, the runtime should record reviewer failure and AMF-252 should fall
# back to the technically usable proposer diff. Use the override only when
# you intentionally change the reviewer transport/model capability.
$ReviewerResponseFormat = if ($ReviewerResponseFormatOverride) {
    $ReviewerResponseFormatOverride.Trim().ToLowerInvariant()
}
elseif ($env:AMF252_REVIEWER_RESPONSE_FORMAT) {
    $env:AMF252_REVIEWER_RESPONSE_FORMAT.Trim().ToLowerInvariant()
}
elseif ($ReviewerModel -ieq "Llama-3.3-70B-Instruct") {
    "json_object"
}
else {
    "json_schema"
}

if ($ReviewerResponseFormat -notin @("", "json_object", "json_schema")) {
    throw "Reviewer response format must be '', 'json_object', or 'json_schema'."
}

$ReviewerIsLlama = ($ReviewerModel -ieq "Llama-3.3-70B-Instruct")
$ReviewerSupportsJsonSchema = (
    $ReviewerResponseFormat -eq "json_schema" -and
    -not $ReviewerIsLlama
)
$ReviewerSupportsJsonObject = ($ReviewerResponseFormat -in @("json_object", "json_schema"))
$ReviewerSupportsStructuredOutputs = $ReviewerSupportsJsonSchema

$ReviewerCapabilityWarning = (
    $ReviewerIsLlama -and
    $ReviewerResponseFormat -in @("json_object", "json_schema")
)


# ---------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------

$BackendHost = "127.0.0.1"
$BackendPort = "8000"

$BackendUrl = "http://${BackendHost}:${BackendPort}"


# =====================================================================
# Helpers
# =====================================================================

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    [Environment]::SetEnvironmentVariable(
        $Name,
        $Value,
        "Process"
    )

    Set-Item `
        -Path "Env:$Name" `
        -Value $Value
}


function Convert-SecureStringToPlainText {
    param(
        [Parameter(Mandatory = $true)]
        [SecureString]$SecureValue
    )

    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR(
        $SecureValue
    )

    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}


function Assert-Directory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Name not found: $Path"
    }
}


function Assert-File {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Name not found: $Path"
    }
}

function Assert-NoInvalidUowConnectionAccess {
    $pattern = '(^|[^A-Za-z0-9_])(uow|write_uow|hb_uow|chk_uow)\._connection\b'

    $hits = @(
        Get-ChildItem -LiteralPath $RepoRoot -Recurse -Force -File -Filter "*.py" |
        Where-Object {
            $_.FullName -notmatch '\\(\.git|\.venv|venv|node_modules|dist|build)\\'
        } |
        Select-String -Pattern $pattern
    )

    if ($hits.Count -gt 0) {
        $details = $hits | ForEach-Object {
            "$($_.Path):$($_.LineNumber):$($_.Line.Trim())"
        }
        throw "Invalid UoW private connection access remains:`n$($details -join "`n")"
    }

    Write-Host "UoW connection preflight OK" -ForegroundColor Green
}

function Assert-RepairAssistantOpenApiAndSQLitePreflight {
    $script = @'
import hashlib, json, sqlite3, tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from migration_factory.control_tower.adapters.fastapi import create_app
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteControlTowerUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import SqliteV2JobRepository, V2MigrationJobRecord
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import SqliteV2RepairRepository, V2RepairProposalRecord
from migration_factory.control_tower.domain.checksums import utc_now_text

db = Path(tempfile.gettempdir()) / ("amf252-assistant-preflight-" + next(tempfile._get_candidate_names()) + ".sqlite3")
conn = sqlite3.connect(str(db), check_same_thread=False, isolation_level=None)
conn.row_factory = sqlite3.Row
apply_pending_migrations(conn)
now = utc_now_text()
job_id, proposal_id = "preflight-job", "preflight-proposal"
diff = Path(tempfile.gettempdir()) / (proposal_id + ".diff")
diff.write_text("diff --git a/pom.xml b/pom.xml\n", encoding="utf-8")
checksum = hashlib.sha256(diff.read_bytes()).hexdigest()
SqliteV2JobRepository(conn).save(V2MigrationJobRecord(job_id, "setup", "setup", "pipeline", "[]", "created", now, now, None))
SqliteV2RepairRepository(conn).save_proposal(V2RepairProposalRecord(
    proposal_id, "command", "failure", "hypothesis", "patch", '["pom.xml"]',
    "user_review_required", None, now, job_id=job_id, attempt_number=1,
    diff_ref=str(diff), diff_checksum=checksum,
))
def uow_factory():
    request_conn = sqlite3.connect(str(db), check_same_thread=False, isolation_level=None)
    request_conn.row_factory = sqlite3.Row
    return SqliteControlTowerUnitOfWork(request_conn, close_connection=True)
app = create_app(uow_factory)
class FakeAssistant:
    calls = 0
    def answer_with_role(self, **kwargs):
        self.calls += 1
        return type("Result", (), {"content": json.dumps({"action":"ANSWER_ONLY","assistant_message":"preflight response","revision_instruction":"","constraints":[],"target_files":[],"target_coordinates":[],"requires_clarification":False}), "success":True, "failure_reason":"", "redacted_summary":"preflight", "primary_http_status":"", "fallback_http_status":"", "response_format_used":"json_object"})()
fake = FakeAssistant()
app.state.v2_assistant_model_client = fake
paths = set(app.openapi().get("paths", {}))
required = {
    "/v1/v2/jobs/{job_id}/repair/proposals/{proposal_id}/assistant/messages"
}
missing = required - paths
if missing: raise SystemExit("missing OpenAPI routes: " + repr(sorted(missing)))
client = TestClient(app)
headers = {"Host":"127.0.0.1:8000", "Origin":"http://127.0.0.1:3000", "X-Control-Tower-Client":"control-tower-frontend"}
get_response = client.get(f"/v1/v2/jobs/{job_id}/repair/proposals/{proposal_id}/assistant/messages", headers={"Host":"127.0.0.1:8000"})
if get_response.status_code != 200 or get_response.json().get("messages") != []: raise SystemExit("Assistant GET preflight failed")
conn.commit()
post_response = client.post(f"/v1/v2/jobs/{job_id}/repair/proposals/{proposal_id}/assistant/messages", headers=headers, json={"message":"Explain", "idempotency_key":"preflight-idempotency", "base_diff_checksum":checksum})
if post_response.status_code != 200 or fake.calls != 1: raise SystemExit("Assistant POST/call-once preflight failed: status=" + str(post_response.status_code) + " calls=" + str(fake.calls) + " body=" + post_response.text[:500])
post_body = post_response.json()
if post_body.get("status") != "answered" or post_body.get("action") != "ANSWER_ONLY": raise SystemExit("Assistant POST contract preflight failed: " + post_response.text[:500])
history_response = client.get(f"/v1/v2/jobs/{job_id}/repair/proposals/{proposal_id}/assistant/messages", headers={"Host":"127.0.0.1:8000"})
if history_response.status_code != 200: raise SystemExit("Assistant GET-after-POST preflight failed: " + history_response.text[:500])
history = history_response.json().get("messages")
if len(history or []) != 2: raise SystemExit("Assistant history count preflight failed: " + repr(history))
if [item.get("role") for item in history] != ["user", "assistant"]: raise SystemExit("Assistant history order preflight failed: " + repr(history))
if history[0].get("message") != "Explain" or history[0].get("action") is not None: raise SystemExit("Assistant user history DTO preflight failed: " + repr(history[0]))
if history[1].get("message") != "preflight response" or history[1].get("action") != "ANSWER_ONLY": raise SystemExit("Assistant assistant history DTO preflight failed: " + repr(history[1]))
if not all(item.get("message_id") and item.get("job_id") == job_id and item.get("proposal_id") == proposal_id and item.get("status") and item.get("created_at") for item in history): raise SystemExit("Assistant history fields preflight failed: " + repr(history))
if len(conn.execute("SELECT 1 FROM repair_assistant_messages WHERE job_id=?", (job_id,)).fetchall()) != 2: raise SystemExit("Assistant persistence preflight failed")
print("OpenAPI Repair Assistant route preflight OK")
print("Temporary SQLite Assistant GET/POST preflight OK")
conn.close()
db.unlink(missing_ok=True); diff.unlink(missing_ok=True)
'@
    $path = Join-Path $env:TEMP ("amf252-assistant-preflight-" + [guid]::NewGuid().ToString("N") + ".py")
    try {
        [IO.File]::WriteAllText($path, $script, (New-Object Text.UTF8Encoding($false)))
        & py.exe $path
        if ($LASTEXITCODE -ne 0) { throw "Repair Assistant OpenAPI/SQLite preflight failed." }
    } finally { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
}


function Assert-Command {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $command = Get-Command `
        $Name `
        -ErrorAction SilentlyContinue

    if ($null -eq $command) {
        throw "Required command not found on PATH: $Name"
    }
}


function Assert-TcpPortAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostAddress,

        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $listener = $null

    try {
        $ipAddress = [System.Net.IPAddress]::Parse($HostAddress)
        $listener = New-Object System.Net.Sockets.TcpListener($ipAddress, $Port)
        $listener.Start()
    }
    catch {
        throw (
            "Backend port is not available: " +
            "${HostAddress}:${Port}. " +
            "Stop any previous backend process before starting this runtime proof."
        )
    }
    finally {
        if ($null -ne $listener) {
            try {
                $listener.Stop()
            }
            catch {
                # Best-effort cleanup only.
            }
        }
    }
}


function Add-PathIfExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathToAdd
    )

    Assert-Directory `
        -Name "PATH entry" `
        -Path $PathToAdd

    $parts = $env:Path -split ";" |
        Where-Object {
            $_ -and $_.Trim()
        }

    if ($parts -notcontains $PathToAdd) {
        $env:Path = "$PathToAdd;$env:Path"
    }
}


function Assert-MigrationPreflight {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedMigrationName,

        [Parameter(Mandatory = $true)]
        [string]$OldConflictingMigrationName,

        [Parameter(Mandatory = $true)]
        [string[]]$RequiredMigrationNames
    )

    Assert-Directory `
        -Name "SQLite migrations directory" `
        -Path $Directory

    $expectedMigration = Join-Path `
        $Directory `
        $ExpectedMigrationName

    Assert-File `
        -Name "AMF-252 repair proposal migration" `
        -Path $expectedMigration

    foreach ($requiredName in $RequiredMigrationNames) {
        Assert-File -Name "Required AMF-252 migration $requiredName" -Path (Join-Path $Directory $requiredName)
    }


    $oldConflictingMigration = Join-Path `
        $Directory `
        $OldConflictingMigrationName

    if (Test-Path -LiteralPath $oldConflictingMigration -PathType Leaf) {
        throw (
            "Old conflicting migration still exists: " +
            $oldConflictingMigration
        )
    }


    $migrationRecords = @(
        Get-ChildItem `
            -LiteralPath $Directory `
            -Filter "*.sql" `
            -File |
        ForEach-Object {

            $match = [regex]::Match(
                $_.Name,
                '^(\d{4})_'
            )

            if ($match.Success) {
                [pscustomobject]@{
                    Version = [int]$match.Groups[1].Value
                    Name = $_.Name
                    FullName = $_.FullName
                }
            }
        }
    )


    if ($migrationRecords.Count -eq 0) {
        throw "No numbered SQLite migrations found in: $Directory"
    }


    $duplicateVersions = @(
        $migrationRecords |
        Group-Object `
            -Property Version |
        Where-Object {
            $_.Count -gt 1
        }
    )


    if ($duplicateVersions.Count -gt 0) {

        $duplicateDetails = @(
            $duplicateVersions |
            ForEach-Object {

                $names = (
                    $_.Group |
                    ForEach-Object {
                        $_.Name
                    }
                ) -join ", "

                "version $($_.Name): $names"
            }
        )

        throw (
            "Duplicate SQLite migration versions detected: " +
            ($duplicateDetails -join "; ")
        )
    }


    Write-Host ""
    Write-Host "Migration preflight OK" -ForegroundColor Green
    Write-Host (
        "Unique migration versions: " +
        $migrationRecords.Count
    )
    Write-Host (
        "Required migration found: " +
        $ExpectedMigrationName
    )
}


function Assert-AllMigrationSqlParseable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory
    )

    $parserScript = @'
from pathlib import Path
import sys

from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    discover_migrations,
    split_sql_statements,
)

migrations_dir = Path(sys.argv[1])
failures = []
for migration in discover_migrations(migrations_dir):
    try:
        split_sql_statements(migration.sql)
    except Exception as exc:
        failures.append(f"{migration.path.name}: {type(exc).__name__}: {exc}")

if failures:
    print("\n".join(failures))
    raise SystemExit(1)

print(f"Parsed {len(discover_migrations(migrations_dir))} numbered migrations with the real splitter.")
'@

    $parserScriptPath = Join-Path `
        $env:TEMP `
        ("amf252-migration-parser-" + [guid]::NewGuid().ToString("N") + ".py")

    $parseOutput = @()
    try {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText(
            $parserScriptPath,
            $parserScript,
            $utf8NoBom
        )

        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $parseOutput = @(
                & py.exe $parserScriptPath $Directory 2>&1
            )
            $parserExitCode = [int]$LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $oldPreference
        }
    }
    finally {
        Remove-Item `
            -LiteralPath $parserScriptPath `
            -Force `
            -ErrorAction SilentlyContinue
    }

    if ($parserExitCode -ne 0) {
        throw (
            "Migration SQL parser preflight failed:`n" +
            (($parseOutput | ForEach-Object { [string]$_ }) -join "`n")
        )
    }

    Write-Host ""
    Write-Host "Migration SQL parser preflight OK" -ForegroundColor Green
    $parseOutput | ForEach-Object { Write-Host $_ }
}


function Set-AzureRole {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Role,

        [Parameter(Mandatory = $true)]
        [string]$Model,

        [Parameter(Mandatory = $true)]
        [bool]$SupportsReasoning,

        [Parameter(Mandatory = $true)]
        [string]$MaxOutputTokens,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$ResponseFormat
    )

    $Prefix = "AZURE_OPENAI_$Role"

    Set-EnvValue "${Prefix}_DEPLOYMENT" $Model
    Set-EnvValue "${Prefix}_MODEL" $Model

    Set-EnvValue `
        "${Prefix}_MAX_INPUT_TOKENS" `
        $RoleMaxInputTokens

    Set-EnvValue `
        "${Prefix}_CONTEXT_TOKENS" `
        $RoleMaxInputTokens

    Set-EnvValue `
        "${Prefix}_MAX_CONTEXT_TOKENS" `
        $RoleMaxInputTokens

    Set-EnvValue `
        "${Prefix}_MAX_OUTPUT_TOKENS" `
        $MaxOutputTokens

    Set-EnvValue `
        "${Prefix}_MAX_COMPLETION_TOKENS" `
        $MaxOutputTokens

    Set-EnvValue `
        "${Prefix}_RESPONSE_FORMAT" `
        $ResponseFormat

    if ($SupportsReasoning) {

        Set-EnvValue `
            "${Prefix}_SUPPORTS_REASONING_EFFORT" `
            "true"

        Set-EnvValue `
            "${Prefix}_REASONING_EFFORT" `
            $RuntimeReasoningEffort
    }
    else {

        Set-EnvValue `
            "${Prefix}_SUPPORTS_REASONING_EFFORT" `
            "false"

        Set-EnvValue `
            "${Prefix}_REASONING_EFFORT" `
            ""
    }
}


function Set-MigrationRole {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Role,

        [Parameter(Mandatory = $true)]
        [string]$Model,

        [Parameter(Mandatory = $true)]
        [bool]$SupportsReasoning,

        [Parameter(Mandatory = $true)]
        [string]$MaxOutputTokens,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$ResponseFormat,

        [Parameter(Mandatory = $true)]
        [bool]$SupportsJsonObject,

        [Parameter(Mandatory = $true)]
        [bool]$SupportsJsonSchema,

        [Parameter(Mandatory = $true)]
        [bool]$SupportsStructuredOutputs
    )

    $Prefix = "AI_MIGRATION_$Role"

    Set-EnvValue "${Prefix}_PROVIDER" "azure_openai"
    Set-EnvValue "${Prefix}_MODEL" $Model
    Set-EnvValue "${Prefix}_MODEL_DISPLAY_NAME" $Model
    Set-EnvValue "${Prefix}_ENDPOINT_TYPE" "chat_completions"

    Set-EnvValue `
        "${Prefix}_RESPONSE_FORMAT" `
        $ResponseFormat

    Set-EnvValue `
        "${Prefix}_SUPPORTS_JSON_OBJECT" `
        ($(if ($SupportsJsonObject) { "true" } else { "false" }))

    Set-EnvValue `
        "${Prefix}_SUPPORTS_JSON_SCHEMA" `
        ($(if ($SupportsJsonSchema) { "true" } else { "false" }))

    Set-EnvValue `
        "${Prefix}_SUPPORTS_STRUCTURED_OUTPUTS" `
        ($(if ($SupportsStructuredOutputs) { "true" } else { "false" }))

    Set-EnvValue `
        "${Prefix}_SUPPORTS_TEMPERATURE" `
        "false"

    Set-EnvValue `
        "${Prefix}_MAX_INPUT_TOKENS" `
        $RoleMaxInputTokens

    Set-EnvValue `
        "${Prefix}_CONTEXT_TOKENS" `
        $RoleMaxInputTokens

    Set-EnvValue `
        "${Prefix}_MAX_CONTEXT_TOKENS" `
        $RoleMaxInputTokens

    Set-EnvValue `
        "${Prefix}_MAX_OUTPUT_TOKENS" `
        $MaxOutputTokens

    Set-EnvValue `
        "${Prefix}_MAX_COMPLETION_TOKENS" `
        $MaxOutputTokens

    Set-EnvValue `
        "${Prefix}_TIMEOUT_SECONDS" `
        "300"

    if ($SupportsReasoning) {

        Set-EnvValue `
            "${Prefix}_SUPPORTS_REASONING_EFFORT" `
            "true"

        Set-EnvValue `
            "${Prefix}_REASONING_EFFORT" `
            $RuntimeReasoningEffort
    }
    else {

        Set-EnvValue `
            "${Prefix}_SUPPORTS_REASONING_EFFORT" `
            "false"

        Set-EnvValue `
            "${Prefix}_REASONING_EFFORT" `
            ""
    }
}


# =====================================================================
# Model smoke helper
# =====================================================================

function Invoke-TinyModelSmoke {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Endpoint,

        [Parameter(Mandatory = $true)]
        [string]$Model,

        [Parameter(Mandatory = $true)]
        [string]$RoleLabel,

        [Parameter(Mandatory = $true)]
        [bool]$SupportsReasoning,

        [Parameter(Mandatory = $false)]
        [AllowEmptyString()]
        [string]$ResponseFormat = "",

        [switch]$ReviewerContractSmoke
    )

    Write-Host ""
    Write-Host "------------------------------------------------------------"
    Write-Host "Tiny $RoleLabel model smoke call"
    Write-Host "------------------------------------------------------------"

    $url = "$($Endpoint.TrimEnd('/'))/chat/completions"

    $body = [ordered]@{
        model = $Model

        messages = @(
            [ordered]@{
                role = "user"
                content = if ($ReviewerContractSmoke) {
                    'Return JSON RepairReviewerOutput with decision accept, proposed_diff "", proposed_edits [], changed_files [], review_notes [], notes [], risks [], confidence 1, policy_concerns [], reviewed_context_checksum "", reviewed_primary_output_checksum "", reviewed_diff_checksum "".'
                }
                elseif ($ResponseFormat -in @("json_object", "json_schema")) {
                    'Reply with exactly {"ok": true}.'
                }
                else {
                    "Reply with exactly OK."
                }
            }
        )
    }

    if ($ReviewerContractSmoke) {
        $body["max_tokens"] = 1024
    }
    elseif ($SupportsReasoning) {
        $body["max_completion_tokens"] = 256
        $body["reasoning_effort"] = "low"
    }
    else {
        $body["max_tokens"] = 64
    }

    if ($ResponseFormat -eq "json_object") {
        $body["response_format"] = [ordered]@{
            type = "json_object"
        }
    }
    elseif ($ResponseFormat -eq "json_schema") {
        $body["response_format"] = [ordered]@{
            type = "json_schema"
            json_schema = [ordered]@{
                name = "amf252_smoke"
                strict = $true
                schema = [ordered]@{
                    type = "object"
                    properties = [ordered]@{
                        ok = [ordered]@{
                            type = "boolean"
                        }
                    }
                    required = @("ok")
                    additionalProperties = $false
                }
            }
        }
    }

    $json = $body |
        ConvertTo-Json `
            -Depth 20 `
            -Compress


    $tempRequest = Join-Path `
        $env:TEMP `
        ("amf252-smoke-" + [guid]::NewGuid().ToString("N") + ".json")


    $tempResponse = Join-Path `
        $env:TEMP `
        ("amf252-smoke-response-" + [guid]::NewGuid().ToString("N") + ".json")


    $tempError = Join-Path `
        $env:TEMP `
        ("amf252-smoke-error-" + [guid]::NewGuid().ToString("N") + ".txt")


    try {

        $utf8NoBom = New-Object `
            System.Text.UTF8Encoding($false)


        [System.IO.File]::WriteAllText(
            $tempRequest,
            $json,
            $utf8NoBom
        )


        $firstBytes = [System.IO.File]::ReadAllBytes(
            $tempRequest
        )


        if (
            $firstBytes.Length -ge 3 -and
            $firstBytes[0] -eq 0xEF -and
            $firstBytes[1] -eq 0xBB -and
            $firstBytes[2] -eq 0xBF
        ) {
            throw "Smoke request unexpectedly contains UTF-8 BOM."
        }


        $curlArgs = @(
            "--silent",
            "--show-error",

            "--connect-timeout", "10",
            "--max-time", "30",

            "--http1.1",

            "-X", "POST",

            "-H", "Content-Type: application/json",
            "-H", "Accept: application/json",

            "-H", "api-key: $env:AZURE_OPENAI_API_KEY",

            "-o", $tempResponse,
            "-w", "%{http_code}",

            "--data-binary", "@$tempRequest",

            $url
        )


        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"


        try {

            $statusCode = [string](
                & curl.exe @curlArgs 2> $tempError
            )

            $statusCode = $statusCode.Trim()

            $curlExitCode = [int]$LASTEXITCODE
        }
        finally {

            $ErrorActionPreference = $oldPreference
        }


        $responseText = ""

        if (
            Test-Path `
                -LiteralPath $tempResponse `
                -PathType Leaf
        ) {

            $responseText = Get-Content `
                -LiteralPath $tempResponse `
                -Raw `
                -Encoding UTF8
        }


        $stderrText = ""

        if (
            Test-Path `
                -LiteralPath $tempError `
                -PathType Leaf
        ) {

            $stderrText = Get-Content `
                -LiteralPath $tempError `
                -Raw `
                -Encoding UTF8
        }


        if (
            $statusCode -match "^2" -and
            $curlExitCode -eq 0 -and
            -not [string]::IsNullOrWhiteSpace($responseText)
        ) {

            if ($ReviewerContractSmoke) {
                try {
                    $parsed = $responseText | ConvertFrom-Json
                }
                catch {
                    Write-Host ""
                    Write-Host "MODEL SMOKE WARNING" -ForegroundColor Yellow
                    Write-Host "Outer Azure response cannot parse."
                    return $false
                }

                if ($null -eq $parsed.choices -or $parsed.choices.Count -eq 0) {
                    Write-Host ""
                    Write-Host "MODEL SMOKE WARNING" -ForegroundColor Yellow
                    Write-Host "No choices in response."
                    return $false
                }

                $finishReason = [string]$parsed.choices[0].finish_reason
                if ($finishReason -eq "length") {
                    Write-Host ""
                    Write-Host "MODEL SMOKE WARNING" -ForegroundColor Yellow
                    Write-Host "Smoke FAILED: finish_reason = length (output truncated)"
                    return $false
                }

                $content = [string]$parsed.choices[0].message.content
                if ([string]::IsNullOrWhiteSpace($content)) {
                    Write-Host ""
                    Write-Host "MODEL SMOKE WARNING" -ForegroundColor Yellow
                    Write-Host "message.content cannot parse or is empty."
                    return $false
                }

                try {
                    $review = $content | ConvertFrom-Json
                }
                catch {
                    Write-Host ""
                    Write-Host "MODEL SMOKE WARNING" -ForegroundColor Yellow
                    Write-Host "message.content cannot parse as JSON."
                    return $false
                }

                $requiredFields = @(
                    "decision", "proposed_diff", "proposed_edits",
                    "changed_files", "review_notes", "notes", "risks",
                    "confidence", "policy_concerns",
                    "reviewed_context_checksum",
                    "reviewed_primary_output_checksum",
                    "reviewed_diff_checksum"
                )

                $missingFields = @(
                    $requiredFields |
                    Where-Object {
                        $null -eq $review.PSObject.Properties[$_]
                    }
                )

                if ($missingFields.Count -gt 0) {
                    Write-Host ""
                    Write-Host "MODEL SMOKE WARNING" -ForegroundColor Yellow
                    Write-Host "Required reviewer field missing: $($missingFields -join ', ')"
                    return $false
                }

                $validatorScript = @'
import json, sys
from migration_factory.control_tower.application.v2_model_schemas import validate_model_output
payload = json.loads(sys.stdin.read())
validate_model_output("RepairReviewerOutput", payload)
print("Reviewer RepairReviewerOutput schema validation OK")
'@
                $validatorPath = Join-Path $env:TEMP ("amf252-reviewer-validate-" + [guid]::NewGuid().ToString("N") + ".py")
                try {
                    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
                    [System.IO.File]::WriteAllText($validatorPath, $validatorScript, $utf8NoBom)
                    $content | & py.exe $validatorPath
                    if ($LASTEXITCODE -ne 0) {
                        Write-Host ""
                        Write-Host "MODEL SMOKE WARNING" -ForegroundColor Yellow
                        Write-Host "Reviewer schema validation fails."
                        return $false
                    }
                }
                finally {
                    Remove-Item -LiteralPath $validatorPath -Force -ErrorAction SilentlyContinue
                }

                Write-Host ""
                Write-Host "MODEL OK" -ForegroundColor Green
                Write-Host "HTTP: $statusCode"
                Write-Host "Model: $($parsed.model)"
                Write-Host "Content: $content"
                Write-Host "Finish reason: $finishReason"
                Write-Host "Reviewer RepairReviewerOutput contract: OK" -ForegroundColor Green

                return $true
            }

            Write-Host ""
            Write-Host "MODEL OK" -ForegroundColor Green
            Write-Host "HTTP: $statusCode"


            try {

                $parsed = $responseText |
                    ConvertFrom-Json


                Write-Host "Model: $($parsed.model)"


                if (
                    $null -ne $parsed.choices -and
                    $parsed.choices.Count -gt 0
                ) {

                    Write-Host (
                        "Content: " +
                        [string]$parsed.choices[0].message.content
                    )

                    Write-Host (
                        "Finish reason: " +
                        [string]$parsed.choices[0].finish_reason
                    )
                }
            }
            catch {

                Write-Host (
                    "Response received but could not parse JSON."
                )
            }


            return $true
        }


        Write-Host ""
        Write-Host `
            "MODEL SMOKE WARNING" `
            -ForegroundColor Yellow

        Write-Host "HTTP: $statusCode"
        Write-Host "curl exit: $curlExitCode"


        if (
            -not [string]::IsNullOrWhiteSpace(
                $responseText
            )
        ) {

            Write-Host "Response:"
            Write-Host $responseText
        }


        if (
            -not [string]::IsNullOrWhiteSpace(
                $stderrText
            )
        ) {

            Write-Host "stderr:"
            Write-Host $stderrText
        }


        return $false
    }
    catch {

        Write-Host ""
        Write-Host `
            "MODEL SMOKE WARNING" `
            -ForegroundColor Yellow

        Write-Host $_.Exception.Message

        return $false
    }
    finally {

        Remove-Item `
            $tempRequest `
            -Force `
            -ErrorAction SilentlyContinue

        Remove-Item `
            $tempResponse `
            -Force `
            -ErrorAction SilentlyContinue

        Remove-Item `
            $tempError `
            -Force `
            -ErrorAction SilentlyContinue
    }
}


# =====================================================================
# Clear conflicting provider variables
# =====================================================================

if ($PreflightOnly) {
    Assert-MigrationPreflight -Directory $MigrationsDir -ExpectedMigrationName $ExpectedRepairMigrationName -OldConflictingMigrationName $OldConflictingRepairMigrationName -RequiredMigrationNames $RequiredRepairMigrationNames
    Assert-AllMigrationSqlParseable -Directory $MigrationsDir
    Assert-NoInvalidUowConnectionAccess
    Assert-RepairAssistantOpenApiAndSQLitePreflight
    $reviewerPreflight = @'
from migration_factory.control_tower.application.v2_model_schemas import validate_model_output
payload = {"decision":"accept","proposed_diff":"","proposed_edits":[],"changed_files":[],"review_notes":[],"notes":[],"risks":[],"confidence":1,"policy_concerns":[],"reviewed_context_checksum":"","reviewed_primary_output_checksum":"","reviewed_diff_checksum":""}
validate_model_output("RepairReviewerOutput", payload)
print("Reviewer RepairReviewerOutput parser preflight OK")
'@
    $reviewerPath = Join-Path $env:TEMP ("amf252-reviewer-preflight-" + [guid]::NewGuid().ToString("N") + ".py")
    try {
        [IO.File]::WriteAllText($reviewerPath, $reviewerPreflight, (New-Object Text.UTF8Encoding($false)))
        & py.exe $reviewerPath
        if ($LASTEXITCODE -ne 0) { throw "Reviewer contract preflight failed." }
    } finally { Remove-Item -LiteralPath $reviewerPath -Force -ErrorAction SilentlyContinue }
    Write-Host "AMF-252 offline preflight passed" -ForegroundColor Green
    exit 0
}

$VarsToClear = @(
    "AZURE_AI_PROJECT_ENDPOINT",
    "FOUNDRY_PROJECT_ENDPOINT",
    "FOUNDRY_API_KEY",
    "AZURE_AI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "MISTRAL_API_KEY",
    "MISTRAL_ENDPOINT"
)


foreach ($var in $VarsToClear) {

    Remove-Item `
        "Env:$var" `
        -ErrorAction SilentlyContinue
}


# =====================================================================
# Azure OpenAI
# =====================================================================

Set-EnvValue `
    "AZURE_OPENAI_ENDPOINT" `
    $AzureOpenAIEndpoint


if (
    [string]::IsNullOrWhiteSpace(
        $env:AZURE_OPENAI_API_KEY
    )
) {

    $secureAzureKey = Read-Host `
        "Paste Azure OpenAI API key" `
        -AsSecureString


    $azureKeyPlain = Convert-SecureStringToPlainText `
        $secureAzureKey


    if (
        [string]::IsNullOrWhiteSpace(
            $azureKeyPlain
        )
    ) {

        throw "AZURE_OPENAI_API_KEY is empty."
    }


    Set-EnvValue `
        "AZURE_OPENAI_API_KEY" `
        $azureKeyPlain
}


# =====================================================================
# Azure role environment
# =====================================================================

Set-AzureRole `
    -Role "PROPOSER" `
    -Model $ProposerModel `
    -SupportsReasoning $true `
    -MaxOutputTokens $ProposerMaxOutputTokens `
    -ResponseFormat $RuntimeResponseFormat


Set-AzureRole `
    -Role "MAIN" `
    -Model $MainModel `
    -SupportsReasoning $true `
    -MaxOutputTokens $MainMaxOutputTokens `
    -ResponseFormat $RuntimeResponseFormat


Set-AzureRole `
    -Role "ASSISTANT" `
    -Model $MainModel `
    -SupportsReasoning $true `
    -MaxOutputTokens $AssistantMaxOutputTokens `
    -ResponseFormat $RuntimeResponseFormat


Set-AzureRole `
    -Role "REVIEWER" `
    -Model $ReviewerModel `
    -SupportsReasoning $false `
    -MaxOutputTokens $ReviewerMaxOutputTokens `
    -ResponseFormat $ReviewerResponseFormat


Set-AzureRole `
    -Role "FALLBACK" `
    -Model $FallbackModel `
    -SupportsReasoning $true `
    -MaxOutputTokens $FallbackMaxOutputTokens `
    -ResponseFormat $RuntimeResponseFormat


# ---------------------------------------------------------------------
# Generic Azure defaults
# ---------------------------------------------------------------------

# Per-role reasoning settings are authoritative. Keep the generic fallback
# empty so a non-reasoning reviewer cannot accidentally inherit "medium".
Set-EnvValue `
    "AZURE_OPENAI_REASONING_EFFORT" `
    ""


Set-EnvValue `
    "AZURE_OPENAI_RESPONSE_FORMAT" `
    $RuntimeResponseFormat


Set-EnvValue `
    "AZURE_OPENAI_MAX_INPUT_TOKENS" `
    $RoleMaxInputTokens


Set-EnvValue `
    "AZURE_OPENAI_MAX_OUTPUT_TOKENS" `
    $DefaultMaxOutputTokens


Set-EnvValue `
    "AZURE_OPENAI_MAX_COMPLETION_TOKENS" `
    $DefaultMaxOutputTokens

# V2ModelRoleRouter fallback is disabled by default unless this Control Tower
# setting is explicitly enabled.
Set-EnvValue `
    "CONTROL_TOWER_AZURE_FOUNDRY_FALLBACK_ENABLED" `
    "true"


# =====================================================================
# AI_MIGRATION role environment
# =====================================================================

Set-EnvValue `
    "AI_MIGRATION_DEFAULT_MAX_INPUT_TOKENS" `
    $RoleMaxInputTokens


Set-EnvValue `
    "AI_MIGRATION_DEFAULT_CONTEXT_TOKENS" `
    $RoleMaxInputTokens


Set-EnvValue `
    "AI_MIGRATION_DEFAULT_MAX_CONTEXT_TOKENS" `
    $RoleMaxInputTokens


Set-EnvValue `
    "AI_MIGRATION_DEFAULT_MAX_OUTPUT_TOKENS" `
    $DefaultMaxOutputTokens


Set-EnvValue `
    "AI_MIGRATION_DEFAULT_MAX_COMPLETION_TOKENS" `
    $DefaultMaxOutputTokens


Set-EnvValue `
    "AI_MIGRATION_DEFAULT_RESPONSE_FORMAT" `
    $RuntimeResponseFormat


Set-MigrationRole `
    -Role "MAIN" `
    -Model $MainModel `
    -SupportsReasoning $true `
    -MaxOutputTokens $MainMaxOutputTokens `
    -ResponseFormat $RuntimeResponseFormat `
    -SupportsJsonObject $true `
    -SupportsJsonSchema $true `
    -SupportsStructuredOutputs $true


Set-MigrationRole `
    -Role "PROPOSER" `
    -Model $ProposerModel `
    -SupportsReasoning $true `
    -MaxOutputTokens $ProposerMaxOutputTokens `
    -ResponseFormat $RuntimeResponseFormat `
    -SupportsJsonObject $true `
    -SupportsJsonSchema $true `
    -SupportsStructuredOutputs $true

Set-MigrationRole `
    -Role "ASSISTANT" `
    -Model $MainModel `
    -SupportsReasoning $true `
    -MaxOutputTokens $AssistantMaxOutputTokens `
    -ResponseFormat $RuntimeResponseFormat `
    -SupportsJsonObject $true `
    -SupportsJsonSchema $true `
    -SupportsStructuredOutputs $true


Set-MigrationRole `
    -Role "REVIEWER" `
    -Model $ReviewerModel `
    -SupportsReasoning $false `
    -MaxOutputTokens $ReviewerMaxOutputTokens `
    -ResponseFormat $ReviewerResponseFormat `
    -SupportsJsonObject $ReviewerSupportsJsonObject `
    -SupportsJsonSchema $ReviewerSupportsJsonSchema `
    -SupportsStructuredOutputs $ReviewerSupportsStructuredOutputs


Set-MigrationRole `
    -Role "FALLBACK" `
    -Model $FallbackModel `
    -SupportsReasoning $true `
    -MaxOutputTokens $FallbackMaxOutputTokens `
    -ResponseFormat $RuntimeResponseFormat `
    -SupportsJsonObject $true `
    -SupportsJsonSchema $true `
    -SupportsStructuredOutputs $true


# =====================================================================
# Java / Maven / Git
# =====================================================================

Assert-Directory `
    "JAVA11_HOME" `
    $Java11Home

Assert-Directory `
    "JAVA17_HOME" `
    $Java17Home

Assert-Directory `
    "JAVA21_HOME" `
    $Java21Home


Assert-Directory `
    "MAVEN_HOME" `
    $MavenHome

Assert-File `
    "MAVEN_CMD" `
    "$MavenHome\bin\mvn.cmd"


Assert-Directory `
    "Git cmd" `
    $GitCmd

Assert-Directory `
    "Git bin" `
    $GitBin

Assert-File `
    "Git executable" `
    "$GitCmd\git.exe"


Set-EnvValue `
    "JAVA11_HOME" `
    $Java11Home

Set-EnvValue `
    "JAVA17_HOME" `
    $Java17Home

Set-EnvValue `
    "JAVA21_HOME" `
    $Java21Home


# Backend default runtime = Java 17.

Set-EnvValue `
    "JAVA_HOME" `
    $Java17Home


Set-EnvValue `
    "MAVEN_HOME" `
    $MavenHome

Set-EnvValue `
    "MAVEN_CMD" `
    "$MavenHome\bin\mvn.cmd"


Add-PathIfExists `
    "$env:JAVA_HOME\bin"

Add-PathIfExists `
    "$env:MAVEN_HOME\bin"

Add-PathIfExists `
    $GitCmd

Add-PathIfExists `
    $GitBin


# =====================================================================
# Runtime preflight
# =====================================================================

Assert-Command "git.exe"
Assert-Command "curl.exe"
Assert-Command "py.exe"

& py.exe -c "import uvicorn"
if ($LASTEXITCODE -ne 0) {
    throw "Python module 'uvicorn' is not available to py.exe."
}


$CurrentBranchOutput = & git branch --show-current
if ($LASTEXITCODE -ne 0) {
    throw "Could not determine current Git branch."
}
$CurrentBranch = ([string]$CurrentBranchOutput).Trim()
if ([string]::IsNullOrWhiteSpace($CurrentBranch)) {
    $CurrentBranch = "(detached HEAD)"
}

$CurrentCommitOutput = & git rev-parse HEAD
if ($LASTEXITCODE -ne 0) {
    throw "Could not determine current Git commit."
}
$CurrentCommit = ([string]$CurrentCommitOutput).Trim()

$GitStatusLines = @(& git status --porcelain)
if ($LASTEXITCODE -ne 0) {
    throw "Could not determine Git working-tree status."
}
$WorkingTreeDirty = ($GitStatusLines.Count -gt 0)

Assert-TcpPortAvailable `
    -HostAddress $BackendHost `
    -Port ([int]$BackendPort)


Assert-MigrationPreflight `
    -Directory $MigrationsDir `
    -ExpectedMigrationName $ExpectedRepairMigrationName `
    -OldConflictingMigrationName $OldConflictingRepairMigrationName `
    -RequiredMigrationNames $RequiredRepairMigrationNames


Assert-AllMigrationSqlParseable `
    -Directory $MigrationsDir


# =====================================================================
# AMF-252 behavior flags
# =====================================================================

Set-EnvValue `
    "AMF252_MAX_REPAIR_ATTEMPTS" `
    ([string]$MaxRepairAttempts)


# ---------------------------------------------------------------------
# Copilot repair loop OFF
# ---------------------------------------------------------------------

Set-EnvValue `
    "AI_MIGRATION_COPILOT_FAILURE_AGENT_ENABLED" `
    "false"

Set-EnvValue `
    "AI_MIGRATION_COPILOT_REQUIRED" `
    "false"

Set-EnvValue `
    "AI_MIGRATION_COPILOT_PROVIDER" `
    ""

Set-EnvValue `
    "AI_MIGRATION_COPILOT_MODEL" `
    ""

Set-EnvValue `
    "AI_MIGRATION_COPILOT_ASSIST" `
    "off"

Set-EnvValue `
    "AI_MIGRATION_ENABLE_COPILOT_REPORT" `
    "false"


# ---------------------------------------------------------------------
# Human approval required for repair application
# ---------------------------------------------------------------------

Set-EnvValue `
    "AI_MIGRATION_AUTO_APPLY_SAFE_REPAIRS" `
    "false"


# ---------------------------------------------------------------------
# Existing runtime profile
# ---------------------------------------------------------------------

Set-EnvValue `
    "AI_MIGRATION_AUTO_APPROVAL_ENABLED" `
    "true"

Set-EnvValue `
    "AI_MIGRATION_H2_STARTUP_REQUIRED" `
    "false"

Set-EnvValue `
    "AI_MIGRATION_SKIP_ENDPOINT_SMOKE" `
    "true"

Set-EnvValue `
    "AI_MIGRATION_PROOF_LEVEL" `
    "build_test_verified"

Set-EnvValue `
    "AI_MIGRATION_ALLOW_GUARDED_SANDBOX_TRANSFORM" `
    "true"


# ---------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------

Set-EnvValue `
    "AI_MIGRATION_LOG_LEVEL" `
    "INFO"

Set-EnvValue `
    "AI_MIGRATION_REPAIR_TRACE" `
    "1"

Set-EnvValue `
    "AI_MIGRATION_REPAIR_DEBUG" `
    "1"

Set-EnvValue `
    "AI_MIGRATION_LLM_ACTIVITY_DIAGNOSTICS" `
    "1"


# =====================================================================
# Runtime summary
# =====================================================================

Write-Host ""
Write-Host "============================================================"
Write-Host "AMF-252 MINIMAL BACKEND LAUNCHER"
Write-Host "============================================================"

Write-Host "Repo:               $RepoRoot"
Write-Host "Branch:             $CurrentBranch"
Write-Host "Commit:             $CurrentCommit"
Write-Host "Working tree dirty: $WorkingTreeDirty"

Write-Host "Endpoint:           $AzureOpenAIEndpoint"

Write-Host ""
Write-Host "Models:"
Write-Host "  Proposer:         $ProposerModel"
Write-Host "  Main:             $MainModel"
Write-Host "  Reviewer:         $ReviewerModel"
Write-Host "  Fallback:         $FallbackModel"

Write-Host ""
Write-Host "Input budget:"
Write-Host "  All roles:        $RoleMaxInputTokens"

Write-Host ""
Write-Host "Configured output budgets:"
Write-Host "  Proposer:         $ProposerMaxOutputTokens"
Write-Host "  Main:             $MainMaxOutputTokens"
Write-Host "  Assistant:        $AssistantMaxOutputTokens"
Write-Host "  Reviewer:         $ReviewerMaxOutputTokens"
Write-Host "  Fallback:         $FallbackMaxOutputTokens"

Write-Host ""
Write-Host "Structured output:"
Write-Host "  Main/proposer:    $RuntimeResponseFormat"
Write-Host "  Reviewer requested: $ReviewerResponseFormat"
Write-Host "  Reviewer JSON object: $ReviewerSupportsJsonObject"
Write-Host "  Reviewer JSON schema: $ReviewerSupportsJsonSchema"
Write-Host "  Reviewer structured: $ReviewerSupportsStructuredOutputs"
Write-Host "  Fallback:         $RuntimeResponseFormat"

if ($ReviewerCapabilityWarning) {
    Write-Host ""
    Write-Host "STRONG REVIEWER CAPABILITY WARNING:" -ForegroundColor Yellow
    Write-Host "  Llama-3.3-70B-Instruct is documented as text-only."
    Write-Host "  Requested reviewer format '$ReviewerResponseFormat' may be rejected."
    Write-Host "  Effective JSON Schema and structured-output capabilities are false."
    Write-Host "  Reviewer failure must leave proposer-diff fallback available."
}

Write-Host ""
Write-Host "Reasoning:"
Write-Host "  Proposer:         $RuntimeReasoningEffort"
Write-Host "  Main:             $RuntimeReasoningEffort"
Write-Host "  Assistant:        $RuntimeReasoningEffort"
Write-Host "  Reviewer:         disabled / omitted"
Write-Host "  Fallback:         $RuntimeReasoningEffort"

Write-Host ""
Write-Host "Repair attempts:"
Write-Host "  Max attempts:     $MaxRepairAttempts"

Write-Host ""
Write-Host "Fallback routing:"
Write-Host "  Enabled:          true"

Write-Host ""
Write-Host "Configured timeout:"
Write-Host "  Proposer:         300 seconds"
Write-Host "  Reviewer:         300 seconds"

Write-Host ""
Write-Host "Migration preflight:"
Write-Host "  Latest required:  $ExpectedRepairMigrationName"
Write-Host "  Required count:   $($RequiredRepairMigrationNames.Count)"
Write-Host "  Duplicate check:  passed"

Write-Host ""
Write-Host "Toolchain:"
Write-Host "  JAVA_HOME:        $env:JAVA_HOME"
Write-Host "  MAVEN_CMD:        $env:MAVEN_CMD"

Write-Host ""
Write-Host "Repair policy:"
Write-Host "  Copilot repair:   disabled"
Write-Host "  Auto repair apply: disabled"
Write-Host "  Human approval:   required"

Write-Host ""
Write-Host "Backend URL:        $BackendUrl"
Write-Host "Port preflight:     available"

Write-Host ""


Assert-NoInvalidUowConnectionAccess
Assert-RepairAssistantOpenApiAndSQLitePreflight

# =====================================================================
# Model smoke
# =====================================================================

$proposerSmokePassed = $true
$reviewerSmokePassed = $true
$mainSmokePassed = $true
$assistantSmokePassed = $true
$fallbackSmokePassed = $true


if (-not $SkipModelSmokeTest) {

    $proposerSmokePassed = Invoke-TinyModelSmoke `
        -Endpoint $AzureOpenAIEndpoint `
        -Model $ProposerModel `
        -RoleLabel "Proposer" `
        -SupportsReasoning $true `
        -ResponseFormat $RuntimeResponseFormat

    $mainSmokePassed = Invoke-TinyModelSmoke -Endpoint $AzureOpenAIEndpoint -Model $MainModel -RoleLabel "Main" -SupportsReasoning $true -ResponseFormat $RuntimeResponseFormat
    $assistantSmokePassed = Invoke-TinyModelSmoke -Endpoint $AzureOpenAIEndpoint -Model $MainModel -RoleLabel "Assistant" -SupportsReasoning $true -ResponseFormat $RuntimeResponseFormat
    $fallbackSmokePassed = Invoke-TinyModelSmoke -Endpoint $AzureOpenAIEndpoint -Model $FallbackModel -RoleLabel "Fallback" -SupportsReasoning $true -ResponseFormat $RuntimeResponseFormat

    if (-not $SkipReviewerSmokeTest) {
        $reviewerSmokePassed = Invoke-TinyModelSmoke `
            -Endpoint $AzureOpenAIEndpoint `
            -Model $ReviewerModel `
            -RoleLabel "Reviewer" `
            -SupportsReasoning $false `
            -ResponseFormat $ReviewerResponseFormat `
            -ReviewerContractSmoke
    }
    else {
        Write-Host ""
        Write-Host `
            "Reviewer smoke skipped." `
            -ForegroundColor Yellow
    }
}
else {

    Write-Host ""
    Write-Host `
        "All model smokes skipped." `
        -ForegroundColor Yellow
}


$modelSmokePassed = ($proposerSmokePassed -and $mainSmokePassed -and $assistantSmokePassed -and $fallbackSmokePassed -and $reviewerSmokePassed)

if (-not $modelSmokePassed) {

    if ($RequireSmokeSuccess) {

        throw (
            "One or more model smokes failed and " +
            "-RequireSmokeSuccess was requested. " +
            "Backend not started."
        )
    }


    Write-Host ""

    Write-Host `
        "One or more model smokes failed, but backend startup will continue." `
        -ForegroundColor Yellow
}


# =====================================================================
# Start backend directly
#
# IMPORTANT:
# This runs Uvicorn in the foreground.
#
# The PowerShell window stays open.
# Backend logs remain visible.
# No hidden process.
# No background job.
# =====================================================================

Write-Host ""
Write-Host "============================================================"
Write-Host "STARTING BACKEND"
Write-Host "============================================================"

Write-Host "URL:     $BackendUrl"
$LogDirectory = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$BackendLogPath = Join-Path $LogDirectory ("amf252-backend-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
Write-Host "Log:     $BackendLogPath"

Write-Host ""

Write-Host "The backend is running when Uvicorn prints:"

Write-Host (
    "Uvicorn running on " +
    "http://${BackendHost}:${BackendPort}"
)

Write-Host ""


$previousErrorActionPreference = $ErrorActionPreference
$BackendExitCode = 0

try {
    $ErrorActionPreference = "Continue"
    $env:PYTHONUNBUFFERED = "1"

    & py.exe -u -m uvicorn `
        migration_factory.control_tower.adapters.fastapi.dev_app:app `
        --host $BackendHost `
        --port $BackendPort `
        --log-level info `
        2>&1 |
        Tee-Object -FilePath $BackendLogPath

    $BackendExitCode = [int]$LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}


if ($BackendExitCode -ne 0) {

    Write-Host ""

    Write-Host `
        "BACKEND EXITED WITH ERROR" `
        -ForegroundColor Red

    Write-Host "Exit code: $BackendExitCode"

    exit $BackendExitCode
}


exit 0
