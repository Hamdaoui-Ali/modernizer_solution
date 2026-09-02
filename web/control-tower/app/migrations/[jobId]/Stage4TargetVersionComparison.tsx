"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { applyStage4TargetVersionChanges, getLatestTargetVersionUpdate, getV2RootPomPreview } from "../../../lib/controlTowerApi";
import type { Stage4TargetVersionApplyResponse } from "../../../lib/contracts";

type ComparisonStatus = "matches" | "different" | "missing_in_pom" | "no_explicit_pom_version" | "blocked";

export type TargetVersionRow = {
  rowNumber: number;
  coordinate: string;
  groupId: string;
  artifactId: string;
  targetVersion: string;
};

export type TargetVersionComparisonRow = TargetVersionRow & {
  pomVersion: string | null;
  versionSource: string;
  status: ComparisonStatus;
  reason: string;
  canApply: boolean;
};

type Props = {
  jobId: string;
  comparisonAvailable: boolean;
  rootPomStageIndex?: number;
  refreshKey?: number;
};

type PomVersion = {
  version: string | null;
  source: string;
  duplicate: boolean;
};

type ZipEntry = {
  name: string;
  compressionMethod: number;
  compressedData: Uint8Array;
};

const TARGET_VERSION_FILE_ACCEPT =
  ".csv,text/csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export default function Stage4TargetVersionComparison({ jobId, comparisonAvailable, rootPomStageIndex = 1, refreshKey = 0 }: Props) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [fileName, setFileName] = useState("");
  const [rows, setRows] = useState<TargetVersionComparisonRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState<Stage4TargetVersionApplyResponse | null>(null);
  const [pomChecksum, setPomChecksum] = useState<string | null>(null);
  const [latestUpdate, setLatestUpdate] = useState<Record<string, unknown> | null>(null);
  const [latestValidation, setLatestValidation] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getLatestTargetVersionUpdate(jobId).then((response) => {
      if (!cancelled) {
        setLatestUpdate(response.update);
        setLatestValidation(response.validation);
      }
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [jobId, refreshKey]);

  async function loadComparison(targetRows: TargetVersionRow[]) {
    const pomPreview = await getV2RootPomPreview(jobId, rootPomStageIndex);
    const pomText = pomPreview.content ?? pomPreview.preview;
    setPomChecksum(pomPreview.pom_checksum ?? null);
    if (!pomPreview.exists || !pomText.trim()) {
      throw new Error(`Stage ${rootPomStageIndex} root pom.xml is not available yet.`);
    }
    const comparisonRows = compareTargetVersionsToPom(targetRows, pomText);
    if (comparisonRows.length === 0) {
      throw new Error(`No uploaded dependencies matched the Stage ${rootPomStageIndex} root pom.xml.`);
    }
    setRows(comparisonRows);
  }

  async function handleFileSelected(file: File | null) {
    setError(null);
    setRows([]);
    setApplyResult(null);
    setFileName(file?.name ?? "");
    if (!file) return;
    if (!comparisonAvailable) {
      setError("The latest migration stage must complete before comparing target versions.");
      return;
    }
    if (!isSupportedTargetVersionFile(file.name)) {
      setError("Upload a CSV or Excel .xlsx file with groupId, artifactId, and targetVersion columns.");
      return;
    }
    setLoading(true);
    try {
      const targetRows = await parseTargetVersionsFile(file);
      if (targetRows.length === 0) {
        throw new Error("Uploaded file did not contain any dependency rows.");
      }
      await loadComparison(targetRows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to compare target versions.");
    } finally {
      setLoading(false);
    }
  }

  async function handleApplyChanges() {
    const candidates = rows.filter((row) => row.canApply && row.status === "different");
    if (candidates.length === 0) return;
    setError(null);
    setApplyResult(null);
    setApplying(true);
    try {
      const result = await applyStage4TargetVersionChanges(jobId, rootPomStageIndex, {
        idempotency_key: createIdempotencyKey(),
        expected_pom_checksum: pomChecksum ?? "",
        changes: candidates.map((row) => ({
          group_id: row.groupId,
          artifact_id: row.artifactId,
          target_version: row.targetVersion,
        })),
      });
      setApplyResult(result);
      setLatestUpdate({ change_id: result.change_id, status: result.status, applied_count: result.applied_count });
      setLatestValidation(result.validation_id ? { validation_id: result.validation_id, status: "validation_queued" } : null);
      if (result.applied_count > 0) {
        await loadComparison(rows);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to apply target versions.");
    } finally {
      setApplying(false);
    }
  }

  const summary = useMemo(() => summarizeComparison(rows), [rows]);
  const applyableCount = rows.filter((row) => row.canApply && row.status === "different").length;
  const workflowActive = ["validation_queued", "running", "repair_review_required"].includes(String(latestUpdate?.status ?? ""));
  const csvInputDisabled = loading || applying || workflowActive || !comparisonAvailable;
  const validationStatus = String(latestValidation?.status ?? latestUpdate?.status ?? "");
  const buildStatus = String(latestValidation?.build_status ?? "");
  const testStatus = String(latestValidation?.test_status ?? "");
  const repairStatus = String(latestUpdate?.repair_status ?? "");

  return (
    <section className="panel cockpit-panel target-version-panel">
      <h2>Target Dependency Versions</h2>
      <p className="meta">
        Upload a CSV or Excel .xlsx file to compare target dependency versions with the latest completed stage root pom.xml. No file is changed until Change is clicked.
      </p>
      <div className="csv-upload-row">
        <input
          ref={fileInputRef}
          aria-label="Upload target dependency version CSV or Excel file"
          accept={TARGET_VERSION_FILE_ACCEPT}
          data-testid="target-version-csv-input"
          disabled={csvInputDisabled}
          hidden
          type="file"
          onChange={(event) => void handleFileSelected(event.currentTarget.files?.[0] ?? null)}
        />
        <button
          type="button"
          data-testid="target-version-csv-button"
          disabled={csvInputDisabled}
          onClick={() => fileInputRef.current?.click()}
        >
          Choisir fichier
        </button>
        <button type="button" disabled={applying || workflowActive || !pomChecksum || applyableCount === 0} onClick={() => void handleApplyChanges()}>
          {applying ? "Changing..." : "Change"}
        </button>
      </div>
      {!comparisonAvailable && <p className="meta">File comparison unlocks after the latest migration stage completes successfully.</p>}
      {fileName && <p className="meta">File: <code>{fileName}</code></p>}
      {loading && <p className="meta">Reading file and latest stage POM...</p>}
      {error && <p className="target-version-error" role="alert">{error}</p>}
      {latestUpdate && (
        <div className="target-version-progress" role="status" aria-live="polite">
          <div className="progress-heading"><strong>Target-version update</strong><span className={`status-badge ${validationStatus === "validated" ? "completed" : validationStatus === "failed" ? "blocked" : "running"}`}>{validationStatus || "applied"}</span></div>
          <div className="progress-grid">
            <ProgressRow label="POM changes applied" state="completed" />
            <ProgressRow label="Validation queued" state={validationStatus === "validation_queued" ? "active" : "completed"} />
            <ProgressRow label={`Build ${buildStatus ? buildStatus.replaceAll("_", " ").toLowerCase() : "pending"}`} state={buildStatus ? (buildStatus.includes("FAILED") ? "failed" : "completed") : "pending"} />
            <ProgressRow label={`Tests ${testStatus ? testStatus.replaceAll("_", " ").toLowerCase() : "pending"}`} state={testStatus ? (testStatus.includes("FAIL") ? "failed" : "completed") : "pending"} />
            {(validationStatus === "repair_review_required" || validationStatus.includes("repair")) && <ProgressRow label="AMF-252 repair review required" state="active" />}
            {repairStatus === "generation_started" && <ProgressRow label="AI repair generation started" state="active" />}
            {repairStatus === "proposer_completed" && <ProgressRow label="Proposer completed" state="completed" />}
            {repairStatus === "reviewer_completed" && <ProgressRow label="Reviewer completed" state="completed" />}
            {repairStatus === "user_review_required" && <ProgressRow label="User review required" state="active" />}
            {repairStatus === "repair_applying" && <ProgressRow label="Repair applying" state="active" />}
            {repairStatus === "repair_validation_running" && <ProgressRow label="Repair validation running" state="active" />}
            {repairStatus === "validated_after_repair" && <ProgressRow label="Validated after repair" state="completed" />}
            {validationStatus === "validated" && <ProgressRow label="Validated target-version update" state="completed" />}
            {validationStatus === "repair_exhausted" && <ProgressRow label="Repair attempts exhausted" state="failed" />}
          </div>
          <p className="meta">Counts: {Number(latestUpdate.applied_count ?? 0)} applied · {Number(latestUpdate.skipped_count ?? 0)} skipped · {Number(latestUpdate.blocked_count ?? 0)} blocked</p>
        </div>
      )}
      {rows.length > 0 && (
        <>
          <div className="target-version-summary">
            <span className="status-badge completed">{summary.matches} match</span>
            <span className="status-badge running">{summary.different} different</span>
            <span className="status-badge blocked">{summary.noExplicitVersion} managed/no version</span>
            <span className="status-badge blocked">{summary.blocked} blocked</span>
          </div>
          <div className="target-version-table-wrap">
            <table className="target-version-table">
              <thead>
                <tr>
                  <th>Dependency</th>
                  <th>Uploaded target</th>
                  <th>Current POM version</th>
                  <th>Source</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.rowNumber}-${row.coordinate}`} className={`target-version-${row.status}`}>
                    <td><code>{row.coordinate}</code></td>
                    <td>{row.targetVersion}</td>
                    <td>{row.pomVersion ?? "not explicit"}</td>
                    <td>{row.versionSource}</td>
                    <td>{row.reason || formatComparisonStatus(row.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {applyResult && (
        <div className="target-version-apply-result" role="status">
          <strong>{applyResult.message}</strong>
          <p className="meta">
            Applied {applyResult.applied_count}, skipped {applyResult.skipped_count}, blocked {applyResult.blocked_count}.
          </p>
          {applyResult.items.length > 0 && (
            <ul>
              {applyResult.items.map((item) => (
                <li key={item.coordinate}>{item.coordinate}: {item.status} ({item.reason})</li>
              ))}
            </ul>
          )}
        </div>
      )}
      <style>{`
        .target-version-panel { max-height: 560px; }
        .csv-upload-row { align-items: center; display: flex; flex-wrap: wrap; gap: 0.6rem; margin: 0.75rem 0; }
        .csv-upload-row button { border: 1px solid #1f6f43; background: #1f6f43; color: #fff; border-radius: 4px; padding: 0.45rem 0.8rem; font-weight: 700; }
        .csv-upload-row button:disabled { opacity: 0.55; cursor: not-allowed; }
        .target-version-error { background: #fff0f0; border: 1px solid #e7b4b4; border-radius: 6px; color: #9f1d1d; font-size: 0.86rem; padding: 0.65rem 0.75rem; }
        .target-version-summary { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.75rem 0; }
        .target-version-table-wrap { overflow: auto; }
        .target-version-table { border-collapse: collapse; font-size: 0.85rem; min-width: 760px; width: 100%; }
        .target-version-table th, .target-version-table td { border-bottom: 1px solid #e6ece9; padding: 0.55rem 0.45rem; text-align: left; vertical-align: top; }
        .target-version-table th { color: #44524c; font-weight: 800; }
        .target-version-table code { overflow-wrap: anywhere; white-space: normal; }
        .target-version-different td { background: #fffaf0; }
        .target-version-missing_in_pom td { background: #fff4f4; }
        .target-version-no_explicit_pom_version td, .target-version-blocked td { background: #faf7ff; }
        .target-version-apply-result { border: 1px solid #b7d7c1; border-radius: 6px; background: #f1fbf4; margin-top: 0.75rem; padding: 0.75rem; }
        .target-version-progress { border: 1px solid #d7e3de; border-left: 4px solid #1f6f43; border-radius: 8px; background: #fbfdfc; margin: 0.85rem 0; padding: 0.85rem; }
        .progress-heading { align-items: center; display: flex; justify-content: space-between; gap: 0.75rem; }
        .progress-grid { display: grid; gap: 0.35rem; margin: 0.7rem 0; }
        .progress-row { align-items: center; display: flex; gap: 0.5rem; font-size: 0.86rem; }
        .progress-icon { width: 1rem; text-align: center; }
        .progress-row.active .progress-icon { animation: target-version-pulse 1.2s ease-in-out infinite; color: #a05a00; }
        .progress-row.failed { color: #9f1d1d; }
        @keyframes target-version-pulse { 50% { opacity: 0.35; } }
        @media (prefers-reduced-motion: reduce) { .progress-row.active .progress-icon { animation: none; } }
      `}</style>
    </section>
  );
}

export async function parseTargetVersionsFile(file: File): Promise<TargetVersionRow[]> {
  const fileName = file.name.toLowerCase();
  if (fileName.endsWith(".csv")) {
    return parseTargetVersionsCsv(await file.text());
  }
  if (fileName.endsWith(".xlsx")) {
    return parseTargetVersionsXlsx(await file.arrayBuffer());
  }
  throw new Error("Upload a CSV or Excel .xlsx file with groupId, artifactId, and targetVersion columns.");
}

export async function parseTargetVersionsXlsx(workbookBytes: ArrayBuffer): Promise<TargetVersionRow[]> {
  const entries = parseZipEntries(workbookBytes);
  const workbookXml = await readZipText(entries, "xl/workbook.xml");
  const relsXml = await readZipText(entries, "xl/_rels/workbook.xml.rels");
  const sheetPath = resolveFirstWorksheetPath(workbookXml, relsXml);
  const sharedStringsXml = entries.has("xl/sharedStrings.xml")
    ? await readZipText(entries, "xl/sharedStrings.xml")
    : "";
  const sheetXml = await readZipText(entries, sheetPath);
  return parseTargetVersionRows(parseWorksheetRows(sheetXml, parseSharedStrings(sharedStringsXml)), "Excel workbook", { lenientRows: true });
}

function isSupportedTargetVersionFile(fileName: string): boolean {
  const normalized = fileName.toLowerCase();
  return normalized.endsWith(".csv") || normalized.endsWith(".xlsx");
}

function resolveFirstWorksheetPath(workbookXml: string, relsXml: string): string {
  const sheetTag = /<sheet\b[^>]*>/i.exec(workbookXml)?.[0];
  const relationshipId = sheetTag ? extractXmlAttribute(sheetTag, "r:id") : null;
  if (relationshipId) {
    for (const relationship of relsXml.matchAll(/<Relationship\b[^>]*>/gi)) {
      const tag = relationship[0];
      if (extractXmlAttribute(tag, "Id") === relationshipId) {
        const target = extractXmlAttribute(tag, "Target");
        if (target) return normalizeZipPath(target.startsWith("/") ? target.slice(1) : `xl/${target}`);
      }
    }
  }
  return "xl/worksheets/sheet1.xml";
}

function parseSharedStrings(sharedStringsXml: string): string[] {
  if (!sharedStringsXml) return [];
  return [...sharedStringsXml.matchAll(/<si\b[^>]*>([\s\S]*?)<\/si>/gi)].map((match) => readTextNodes(match[1]));
}

function parseWorksheetRows(sheetXml: string, sharedStrings: string[]): string[][] {
  return [...sheetXml.matchAll(/<row\b[^>]*>([\s\S]*?)<\/row>/gi)]
    .map((rowMatch) => {
      const row: string[] = [];
      let fallbackColumnIndex = 0;
      for (const cellMatch of rowMatch[1].matchAll(/<c\b([^>]*)>([\s\S]*?)<\/c>/gi)) {
        const attrs = cellMatch[1];
        const cellBody = cellMatch[2];
        const columnIndex = columnIndexFromCellRef(extractXmlAttribute(attrs, "r")) ?? fallbackColumnIndex;
        row[columnIndex] = readCellValue(attrs, cellBody, sharedStrings);
        fallbackColumnIndex = columnIndex + 1;
      }
      return row.map((value) => value ?? "");
    })
    .filter((row) => row.some((cell) => cell.trim().length > 0));
}

function readCellValue(attrs: string, cellBody: string, sharedStrings: string[]): string {
  const type = extractXmlAttribute(attrs, "t");
  if (type === "s") {
    const index = Number(readFirstTagText(cellBody, "v"));
    return Number.isInteger(index) ? sharedStrings[index] ?? "" : "";
  }
  if (type === "inlineStr") {
    return readTextNodes(cellBody);
  }
  return readFirstTagText(cellBody, "v") || readTextNodes(cellBody);
}

function readTextNodes(xmlFragment: string): string {
  return [...xmlFragment.matchAll(/<t\b[^>]*>([\s\S]*?)<\/t>/gi)]
    .map((match) => decodeXmlText(match[1]))
    .join("");
}

function readFirstTagText(xmlFragment: string, tagName: string): string {
  const pattern = new RegExp(`<${tagName}\\b[^>]*>([\\s\\S]*?)<\\/${tagName}>`, "i");
  const match = pattern.exec(xmlFragment);
  return match ? decodeXmlText(match[1].trim()) : "";
}

function columnIndexFromCellRef(cellRef: string | null): number | null {
  const letters = /^[A-Z]+/i.exec(cellRef ?? "")?.[0];
  if (!letters) return null;
  return [...letters.toUpperCase()].reduce((value, letter) => value * 26 + letter.charCodeAt(0) - 64, 0) - 1;
}

function parseZipEntries(workbookBytes: ArrayBuffer): Map<string, ZipEntry> {
  const view = new DataView(workbookBytes);
  const bytes = new Uint8Array(workbookBytes);
  const eocdOffset = findEndOfCentralDirectory(view);
  const centralDirectorySize = view.getUint32(eocdOffset + 12, true);
  const centralDirectoryOffset = view.getUint32(eocdOffset + 16, true);
  const entries = new Map<string, ZipEntry>();
  let offset = centralDirectoryOffset;
  const endOffset = centralDirectoryOffset + centralDirectorySize;
  while (offset < endOffset) {
    if (view.getUint32(offset, true) !== 0x02014b50) {
      throw new Error("Excel .xlsx central directory is invalid.");
    }
    const compressionMethod = view.getUint16(offset + 10, true);
    const compressedSize = view.getUint32(offset + 20, true);
    const fileNameLength = view.getUint16(offset + 28, true);
    const extraLength = view.getUint16(offset + 30, true);
    const commentLength = view.getUint16(offset + 32, true);
    const localHeaderOffset = view.getUint32(offset + 42, true);
    const fileName = decodeUtf8(bytes.slice(offset + 46, offset + 46 + fileNameLength));
    const localNameLength = view.getUint16(localHeaderOffset + 26, true);
    const localExtraLength = view.getUint16(localHeaderOffset + 28, true);
    const dataOffset = localHeaderOffset + 30 + localNameLength + localExtraLength;
    entries.set(normalizeZipPath(fileName), {
      name: normalizeZipPath(fileName),
      compressionMethod,
      compressedData: bytes.slice(dataOffset, dataOffset + compressedSize),
    });
    offset += 46 + fileNameLength + extraLength + commentLength;
  }
  return entries;
}

function findEndOfCentralDirectory(view: DataView): number {
  const minimumOffset = Math.max(0, view.byteLength - 65557);
  for (let offset = view.byteLength - 22; offset >= minimumOffset; offset -= 1) {
    if (view.getUint32(offset, true) === 0x06054b50) return offset;
  }
  throw new Error("Excel .xlsx file is invalid or corrupted.");
}

async function readZipText(entries: Map<string, ZipEntry>, path: string): Promise<string> {
  const entry = entries.get(normalizeZipPath(path));
  if (!entry) throw new Error(`Excel .xlsx file is missing ${path}.`);
  return decodeUtf8(await decompressZipEntry(entry));
}

async function decompressZipEntry(entry: ZipEntry): Promise<Uint8Array> {
  if (entry.compressionMethod === 0) return entry.compressedData;
  if (entry.compressionMethod !== 8) {
    throw new Error("Excel .xlsx file uses an unsupported compression method.");
  }
  if (typeof DecompressionStream === "undefined") {
    throw new Error("Excel .xlsx parsing is not supported by this browser. Export the workbook as CSV and upload it.");
  }
  const stream = new Blob([toArrayBuffer(entry.compressedData)]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function extractXmlAttribute(tag: string, name: string): string | null {
  const pattern = new RegExp(`\\b${escapeRegExp(name)}=(["'])(.*?)\\1`, "i");
  const match = pattern.exec(tag);
  return match ? decodeXmlText(match[2]) : null;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizeZipPath(path: string): string {
  const segments: string[] = [];
  for (const segment of path.replace(/\\/g, "/").split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") segments.pop();
    else segments.push(segment);
  }
  return segments.join("/");
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

function decodeUtf8(bytes: Uint8Array): string {
  return new TextDecoder("utf-8").decode(bytes);
}
export function parseTargetVersionsCsv(csvText: string): TargetVersionRow[] {
  return parseTargetVersionRows(parseCsv(csvText), "CSV");
}

export function parseTargetVersionRows(parsedRows: string[][], sourceLabel: string, options: { lenientRows?: boolean } = {}): TargetVersionRow[] {
  if (parsedRows.length < 2) return [];
  const headers = parsedRows[0].map(normalizeHeader);
  const groupIndex = findHeaderIndex(headers, ["groupid", "group", "group_id"]);
  const artifactIndex = findHeaderIndex(headers, ["artifactid", "artifact", "artifact_id", "dependency"]);
  const versionIndex = findHeaderIndex(headers, ["targetversion", "target_version", "version", "target"]);
  const coordinateIndex = findHeaderIndex(headers, ["coordinate", "gav", "dependencycoordinate", "dependency_coordinate"]);
  if (coordinateIndex < 0 && (groupIndex < 0 || artifactIndex < 0 || versionIndex < 0)) {
    throw new Error(`${sourceLabel} must include either coordinate or groupId, artifactId, and targetVersion columns.`);
  }
  const seen = new Map<string, string>();
  return parsedRows.slice(1).flatMap((cells, index) => {
    const rowNumber = index + 2;
    const coordinateCell = readCell(cells, coordinateIndex);
    const groupCell = readCell(cells, groupIndex);
    const artifactCell = readCell(cells, artifactIndex);
    const versionCell = readCell(cells, versionIndex);
    const coordinateParts = coordinateCell.split(":").map((part) => part.trim()).filter(Boolean);
    const groupId = groupCell || coordinateParts[0] || "";
    const artifactId = artifactCell || coordinateParts[1] || "";
    const targetVersion = versionCell || coordinateParts[2] || "";
    if (!groupId && !artifactId && !targetVersion) return [];
    if (!groupId || !artifactId || !targetVersion) {
      if (options.lenientRows) return [];
      throw new Error(`${sourceLabel} row ${rowNumber} must include groupId, artifactId, and target version.`);
    }
    if (!isSafeCoordinatePart(groupId) || !isSafeCoordinatePart(artifactId)) {
      if (options.lenientRows) return [];
      throw new Error(`${sourceLabel} row ${rowNumber} has an invalid dependency coordinate.`);
    }
    if (!isSafeVersion(targetVersion)) {
      if (options.lenientRows) return [];
      throw new Error(`${sourceLabel} row ${rowNumber} has an invalid target version.`);
    }
    const coordinate = `${groupId}:${artifactId}`;
    const existing = seen.get(coordinate);
    if (existing && existing !== targetVersion) {
      if (options.lenientRows) return [];
      throw new Error(`${sourceLabel} row ${rowNumber} duplicates ${coordinate} with a different target version.`);
    }
    if (existing) return [];
    seen.set(coordinate, targetVersion);
    return [{ rowNumber, coordinate, groupId, artifactId, targetVersion }];
  });
}

export function compareTargetVersionsToPom(targetRows: TargetVersionRow[], pomText: string): TargetVersionComparisonRow[] {
  const pomVersions = parsePomDependencyVersions(pomText);
  return targetRows.flatMap<TargetVersionComparisonRow>((row) => {
    const pomVersion = pomVersions.get(row.coordinate);
    if (!pomVersion) {
      return [];
    }
    if (pomVersion.duplicate) {
      return [{ ...row, pomVersion: pomVersion.version, versionSource: pomVersion.source, status: "blocked", reason: "Duplicate POM entries require manual review", canApply: false }];
    }
    if (!pomVersion.version) {
      return [{ ...row, pomVersion: null, versionSource: pomVersion.source, status: "no_explicit_pom_version", reason: "No explicit version to update", canApply: false }];
    }
    const matches = normalizeVersion(pomVersion.version) === normalizeVersion(row.targetVersion);
    return [{
      ...row,
      pomVersion: pomVersion.version,
      versionSource: pomVersion.source,
      status: matches ? "matches" : "different",
      reason: matches ? "Matches target" : "Different version",
      canApply: !matches && ["dependency", "dependency_management", "property"].includes(pomVersion.source),
    }];
  });
}

function parsePomDependencyVersions(pomText: string): Map<string, PomVersion> {
  const properties = parsePomProperties(pomText);
  const versions = new Map<string, PomVersion>();
  const seen = new Set<string>();
  const dependencyManagementRanges = findXmlBlocksWithSpans(pomText, "dependencyManagement");
  for (const block of findXmlBlocksWithSpans(pomText, "dependency")) {
    const groupId = extractXmlTag(block.text, "groupId");
    const artifactId = extractXmlTag(block.text, "artifactId");
    if (!groupId || !artifactId) continue;
    const coordinate = `${groupId}:${artifactId}`;
    const rawVersion = extractXmlTag(block.text, "version");
    const propertyName = rawVersion ? /^\$\{([^}]+)\}$/.exec(rawVersion.trim())?.[1] : undefined;
    const source = propertyName ? "property" : isInsideRange(block, dependencyManagementRanges) ? "dependency_management" : "dependency";
    const version = propertyName ? properties.get(propertyName) ?? rawVersion : rawVersion;
    const duplicate = seen.has(coordinate);
    seen.add(coordinate);
    versions.set(coordinate, { version, source, duplicate: duplicate || versions.get(coordinate)?.duplicate === true });
  }
  const parentBlock = findXmlBlocksWithSpans(pomText, "parent")[0];
  if (parentBlock) {
    const groupId = extractXmlTag(parentBlock.text, "groupId");
    const artifactId = extractXmlTag(parentBlock.text, "artifactId");
    const version = extractXmlTag(parentBlock.text, "version");
    if (groupId && artifactId && !versions.has(`${groupId}:${artifactId}`)) {
      versions.set(`${groupId}:${artifactId}`, { version, source: "parent", duplicate: false });
    }
  }
  return versions;
}

function parsePomProperties(pomText: string): Map<string, string> {
  const properties = new Map<string, string>();
  const propertiesBlock = findXmlBlocksWithSpans(pomText, "properties")[0]?.text ?? "";
  const tagPattern = /<([\w.-]+)>\s*([^<]+?)\s*<\/\1>/g;
  let match: RegExpExecArray | null;
  while ((match = tagPattern.exec(propertiesBlock)) !== null) {
    properties.set(match[1], decodeXmlText(match[2].trim()));
  }
  return properties;
}

function findXmlBlocksWithSpans(xmlText: string, tagName: string): Array<{ start: number; end: number; text: string }> {
  const pattern = new RegExp(`<${tagName}\\b[^>]*>[\\s\\S]*?<\\/${tagName}>`, "g");
  return [...xmlText.matchAll(pattern)].map((match) => ({ start: match.index ?? 0, end: (match.index ?? 0) + match[0].length, text: match[0] }));
}

function isInsideRange(block: { start: number; end: number }, ranges: Array<{ start: number; end: number }>): boolean {
  return ranges.some((range) => block.start >= range.start && block.end <= range.end);
}

function extractXmlTag(block: string, tagName: string): string | null {
  const pattern = new RegExp(`<${tagName}\\b[^>]*>\\s*([\\s\\S]*?)\\s*<\\/${tagName}>`);
  const match = pattern.exec(block);
  return match ? decodeXmlText(match[1].trim()) : null;
}

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (char === '"' && quoted && next === '"') {
      cell += '"';
      index += 1;
      continue;
    }
    if (char === '"') {
      quoted = !quoted;
      continue;
    }
    if (char === "," && !quoted) {
      row.push(cell.trim());
      cell = "";
      continue;
    }
    if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(cell.trim());
      if (row.some(Boolean)) rows.push(row);
      row = [];
      cell = "";
      continue;
    }
    cell += char;
  }
  row.push(cell.trim());
  if (row.some(Boolean)) rows.push(row);
  return rows;
}

function summarizeComparison(rows: TargetVersionComparisonRow[]) {
  return {
    matches: rows.filter((row) => row.status === "matches").length,
    different: rows.filter((row) => row.status === "different").length,
    noExplicitVersion: rows.filter((row) => row.status === "no_explicit_pom_version").length,
    blocked: rows.filter((row) => row.status === "blocked").length,
  };
}

function formatComparisonStatus(status: ComparisonStatus): string {
  if (status === "matches") return "Matches target";
  if (status === "different") return "Different version";
  if (status === "missing_in_pom") return "Missing from POM";
  if (status === "no_explicit_pom_version") return "No explicit POM version";
  return "Blocked";
}

function findHeaderIndex(headers: string[], names: string[]): number {
  return headers.findIndex((header) => names.includes(header));
}

function normalizeHeader(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]/g, "");
}

function readCell(cells: string[], index: number): string {
  return index >= 0 ? (cells[index] ?? "").trim() : "";
}

function normalizeVersion(value: string): string {
  return value.trim().toLowerCase();
}

function isSafeCoordinatePart(value: string): boolean {
  return /^[A-Za-z0-9_.-]+$/.test(value);
}

function isSafeVersion(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._+\-]*$/.test(value);
}

function decodeXmlText(value: string): string {
  return value
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'");
}

function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `csv-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function ProgressRow({ label, state }: { label: string; state: "completed" | "active" | "pending" | "failed" }) {
  const icon = state === "completed" ? "✓" : state === "failed" ? "✕" : state === "active" ? "●" : "○";
  return <div className={`progress-row ${state}`}><span className="progress-icon" aria-hidden="true">{icon}</span><span>{label}</span></div>;
}
