import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Stage4TargetVersionComparison, {
  compareTargetVersionsToPom,
  parseTargetVersionsCsv,
  parseTargetVersionsFile,
  parseTargetVersionsXlsx,
  type TargetVersionRow,
} from "../app/migrations/[jobId]/Stage4TargetVersionComparison";

describe("Latest-stage target version file upload control", () => {
  it("renders an enabled Choisir fichier button only after the latest stage completes", () => {
    const enabledMarkup = renderToStaticMarkup(
      <Stage4TargetVersionComparison jobId="job-1" comparisonAvailable={true} rootPomStageIndex={3} />,
    );
    const disabledMarkup = renderToStaticMarkup(
      <Stage4TargetVersionComparison jobId="job-1" comparisonAvailable={false} rootPomStageIndex={3} />,
    );

    expect(enabledMarkup).toContain("Choisir fichier");
    expect(enabledMarkup).toContain('data-testid="target-version-csv-input"');
    expect(enabledMarkup).toContain("hidden");
    const enabledButtonStart = enabledMarkup.indexOf("Choisir fichier");
    expect(enabledMarkup.slice(enabledMarkup.lastIndexOf("<button", enabledButtonStart), enabledButtonStart)).not.toContain("disabled");

    const disabledButtonStart = disabledMarkup.indexOf("Choisir fichier");
    expect(disabledMarkup.slice(disabledMarkup.lastIndexOf("<button", disabledButtonStart), disabledButtonStart)).toContain("disabled");
    expect(enabledMarkup).toContain(".xlsx");
    expect(enabledMarkup).toContain("CSV or Excel .xlsx");
    expect(disabledMarkup).toContain("File comparison unlocks after the latest migration stage completes successfully.");
  });
});

describe("Latest-stage target version file comparison", () => {
  it("parses target versions from explicit group/artifact/version columns", () => {
    const rows = parseTargetVersionsCsv([
      "groupId,artifactId,targetVersion",
      "com.fasterxml.jackson.core,jackson-databind,2.17.2",
      "org.springframework.boot,spring-boot-starter-web,4.0.0",
    ].join("\n"));

    expect(rows).toEqual([
      {
        rowNumber: 2,
        coordinate: "com.fasterxml.jackson.core:jackson-databind",
        groupId: "com.fasterxml.jackson.core",
        artifactId: "jackson-databind",
        targetVersion: "2.17.2",
      },
      {
        rowNumber: 3,
        coordinate: "org.springframework.boot:spring-boot-starter-web",
        groupId: "org.springframework.boot",
        artifactId: "spring-boot-starter-web",
        targetVersion: "4.0.0",
      },
    ]);
  });

  it("parses target versions from an Excel xlsx workbook", async () => {
    const workbook = makeStoredXlsxWorkbook([
      ["groupId", "artifactId", "targetVersion"],
      ["com.fasterxml.jackson.core", "jackson-databind", "2.17.2"],
      ["org.springframework.boot", "spring-boot-starter-web", "4.0.0"],
      ["invalid coordinate", "ignored", "1.0.0"],
    ]);

    const rows = await parseTargetVersionsXlsx(workbook);
    const fileRows = await parseTargetVersionsFile(new File([workbook], "targets.xlsx"));

    expect(fileRows).toEqual(rows);
    expect(rows).toEqual([
      {
        rowNumber: 2,
        coordinate: "com.fasterxml.jackson.core:jackson-databind",
        groupId: "com.fasterxml.jackson.core",
        artifactId: "jackson-databind",
        targetVersion: "2.17.2",
      },
      {
        rowNumber: 3,
        coordinate: "org.springframework.boot:spring-boot-starter-web",
        groupId: "org.springframework.boot",
        artifactId: "spring-boot-starter-web",
        targetVersion: "4.0.0",
      },
    ]);
  });
  it("parses target versions from a coordinate column", () => {
    const rows = parseTargetVersionsCsv([
      "coordinate",
      "org.junit.jupiter:junit-jupiter:5.11.4",
    ].join("\n"));

    expect(rows).toEqual([
      {
        rowNumber: 2,
        coordinate: "org.junit.jupiter:junit-jupiter",
        groupId: "org.junit.jupiter",
        artifactId: "junit-jupiter",
        targetVersion: "5.11.4",
      },
    ]);
  });

  it("rejects missing columns and invalid versions", () => {
    expect(() => parseTargetVersionsCsv("name,version\nlib,1.0.0")).toThrow(/must include/);
    expect(() => parseTargetVersionsCsv("groupId,artifactId,targetVersion\ncom.example,lib,1.0 <bad>")).toThrow(/invalid target version/);
  });

  it("rejects duplicate target rows with conflicting versions", () => {
    expect(() => parseTargetVersionsCsv([
      "groupId,artifactId,targetVersion",
      "com.example,lib,1.0.0",
      "com.example,lib,2.0.0",
    ].join("\n"))).toThrow(/duplicates com.example:lib/);
  });

  it("compares target rows with direct, property, managed, and duplicate POM versions while ignoring missing dependencies", () => {
    const targets: TargetVersionRow[] = [
      makeTarget("com.fasterxml.jackson.core", "jackson-databind", "2.17.2"),
      makeTarget("com.google.code.gson", "gson", "2.11.0"),
      makeTarget("org.junit.jupiter", "junit-jupiter", "5.11.4"),
      makeTarget("com.example", "missing", "1.0.0"),
      makeTarget("com.example", "duplicate", "2.0.0"),
    ];
    const pom = `
      <project>
        <properties>
          <jackson.version>2.17.2</jackson.version>
        </properties>
        <dependencyManagement>
          <dependencies>
            <dependency>
              <groupId>org.junit.jupiter</groupId>
              <artifactId>junit-jupiter</artifactId>
              <version>5.10.0</version>
            </dependency>
          </dependencies>
        </dependencyManagement>
        <dependencies>
          <dependency>
            <groupId>com.fasterxml.jackson.core</groupId>
            <artifactId>jackson-databind</artifactId>
            <version>\${jackson.version}</version>
          </dependency>
          <dependency>
            <groupId>com.google.code.gson</groupId>
            <artifactId>gson</artifactId>
            <version>2.9.0</version>
          </dependency>
          <dependency>
            <groupId>com.example</groupId>
            <artifactId>duplicate</artifactId>
            <version>1.0.0</version>
          </dependency>
          <dependency>
            <groupId>com.example</groupId>
            <artifactId>duplicate</artifactId>
            <version>1.1.0</version>
          </dependency>
        </dependencies>
      </project>
    `;

    const comparison = compareTargetVersionsToPom(targets, pom);

    expect(comparison.map((row) => ({
      coordinate: row.coordinate,
      pomVersion: row.pomVersion,
      versionSource: row.versionSource,
      status: row.status,
      canApply: row.canApply,
    }))).toEqual([
      {
        coordinate: "com.fasterxml.jackson.core:jackson-databind",
        pomVersion: "2.17.2",
        versionSource: "property",
        status: "matches",
        canApply: false,
      },
      {
        coordinate: "com.google.code.gson:gson",
        pomVersion: "2.9.0",
        versionSource: "dependency",
        status: "different",
        canApply: true,
      },
      {
        coordinate: "org.junit.jupiter:junit-jupiter",
        pomVersion: "5.10.0",
        versionSource: "dependency_management",
        status: "different",
        canApply: true,
      },
      {
        coordinate: "com.example:duplicate",
        pomVersion: "1.1.0",
        versionSource: "dependency",
        status: "blocked",
        canApply: false,
      },
    ]);
    expect(comparison.some((row) => row.coordinate === "com.example:missing")).toBe(false);
  });
});

function makeStoredXlsxWorkbook(rows: string[][]): ArrayBuffer {
  const sheetRows = rows.map((row, rowIndex) => {
    const cells = row.map((value, columnIndex) => {
      const cellRef = `${columnName(columnIndex)}${rowIndex + 1}`;
      return `<c r="${cellRef}" t="inlineStr"><is><t>${escapeXml(value)}</t></is></c>`;
    }).join("");
    return `<row r="${rowIndex + 1}">${cells}</row>`;
  }).join("");
  return makeStoredZip({
    "xl/workbook.xml": `<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Targets" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    "xl/_rels/workbook.xml.rels": `<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>`,
    "xl/worksheets/sheet1.xml": `<worksheet><sheetData>${sheetRows}</sheetData></worksheet>`,
  });
}

function makeStoredZip(entries: Record<string, string>): ArrayBuffer {
  const encoder = new TextEncoder();
  const localParts: Uint8Array[] = [];
  const centralParts: Uint8Array[] = [];
  let offset = 0;
  for (const [name, content] of Object.entries(entries)) {
    const nameBytes = encoder.encode(name);
    const dataBytes = encoder.encode(content);
    const local = new Uint8Array(30 + nameBytes.length + dataBytes.length);
    const localView = new DataView(local.buffer);
    localView.setUint32(0, 0x04034b50, true);
    localView.setUint16(4, 20, true);
    localView.setUint16(8, 0, true);
    localView.setUint32(14, 0, true);
    localView.setUint32(18, dataBytes.length, true);
    localView.setUint32(22, dataBytes.length, true);
    localView.setUint16(26, nameBytes.length, true);
    local.set(nameBytes, 30);
    local.set(dataBytes, 30 + nameBytes.length);

    const central = new Uint8Array(46 + nameBytes.length);
    const centralView = new DataView(central.buffer);
    centralView.setUint32(0, 0x02014b50, true);
    centralView.setUint16(4, 20, true);
    centralView.setUint16(6, 20, true);
    centralView.setUint16(10, 0, true);
    centralView.setUint32(16, 0, true);
    centralView.setUint32(20, dataBytes.length, true);
    centralView.setUint32(24, dataBytes.length, true);
    centralView.setUint16(28, nameBytes.length, true);
    centralView.setUint32(42, offset, true);
    central.set(nameBytes, 46);

    localParts.push(local);
    centralParts.push(central);
    offset += local.length;
  }

  const centralDirectoryOffset = offset;
  const centralDirectorySize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const eocd = new Uint8Array(22);
  const eocdView = new DataView(eocd.buffer);
  eocdView.setUint32(0, 0x06054b50, true);
  eocdView.setUint16(8, centralParts.length, true);
  eocdView.setUint16(10, centralParts.length, true);
  eocdView.setUint32(12, centralDirectorySize, true);
  eocdView.setUint32(16, centralDirectoryOffset, true);
  return toArrayBuffer(concatenateBytes([...localParts, ...centralParts, eocd]));
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

function concatenateBytes(parts: Uint8Array[]): Uint8Array {
  const output = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    output.set(part, offset);
    offset += part.length;
  }
  return output;
}

function columnName(index: number): string {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    value -= 1;
    name = String.fromCharCode(65 + (value % 26)) + name;
    value = Math.floor(value / 26);
  }
  return name;
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
function makeTarget(groupId: string, artifactId: string, targetVersion: string): TargetVersionRow {
  return {
    rowNumber: 1,
    coordinate: `${groupId}:${artifactId}`,
    groupId,
    artifactId,
    targetVersion,
  };
}