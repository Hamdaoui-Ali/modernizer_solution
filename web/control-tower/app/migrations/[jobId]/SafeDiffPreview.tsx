"use client";

import type { SafeDiffPreview as SafeDiffPreviewType } from "../../../lib/contracts";

export function SafeDiffPreview({
  diff,
}: {
  diff: SafeDiffPreviewType | null;
}) {
  if (!diff) {
    return (
      <div className="safe-diff-missing" data-testid="safe-diff-missing">
        <p className="meta">No diff preview available.</p>
      </div>
    );
  }

  return (
    <div className="safe-diff-preview" data-testid="safe-diff-preview">
      <div className="safe-diff-summary">
        <p className="meta">
          {diff.files.length} file{diff.files.length !== 1 ? "s" : ""} changed
          — +{diff.total_additions} / -{diff.total_deletions}
        </p>
        {diff.truncated && (
          <p className="warning-text" data-testid="truncation-notice">
            Diff truncated. Some content is omitted.
          </p>
        )}
        {diff.checksum_mismatch && (
          <p className="warning-text" data-testid="checksum-mismatch-warning">
            Diff checksum mismatch detected. This proposal cannot be approved until regenerated.
          </p>
        )}
        {diff.redactions.length > 0 && (
          <p className="warning-text" data-testid="redaction-notice">
            {diff.redactions.length} redaction{diff.redactions.length !== 1 ? "s" : ""} applied to this diff.
          </p>
        )}
      </div>
      {diff.files.map((file, fi) => (
        <div key={fi} className="safe-diff-file" data-testid="safe-diff-file">
          <div className="safe-diff-file-header">
            <strong>{file.path}</strong>
            <span className="meta">
              {file.change_type} — +{file.additions} / -{file.deletions}
            </span>
            {file.truncated && <span className="warning-text"> (truncated)</span>}
          </div>
          {file.hunks.length > 0 && (
            <div className="safe-diff-hunks">
              {file.hunks.map((hunk, hi) => (
                <div key={hi} className="safe-diff-hunk" data-testid="safe-diff-hunk">
                  <div className="safe-diff-hunk-header">
                    @@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@
                    {hunk.section_header ? ` ${hunk.section_header}` : ""}
                  </div>
                  <div className="safe-diff-lines">
                    {hunk.lines.map((line, li) => (
                      <div
                        key={li}
                        className={`safe-diff-line safe-diff-line-${line.kind}`}
                        data-testid="safe-diff-line"
                      >
                        <span className="safe-diff-line-numbers">
                          {line.old_line_number != null ? String(line.old_line_number) : " "}
                          {" | "}
                          {line.new_line_number != null ? String(line.new_line_number) : " "}
                        </span>
                        <span className="safe-diff-line-marker">{line.kind === "addition" ? "+" : line.kind === "deletion" ? "-" : " "}</span>
                        <span className="safe-diff-line-text">
                          {line.redacted ? (
                            <span className="redacted-text" data-testid="redacted-line">[redacted]</span>
                          ) : (
                            line.text
                          )}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
