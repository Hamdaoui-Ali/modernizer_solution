"use client";

import { useState } from "react";

export function RepairRevisionDialog({
  open,
  onClose,
  onSubmit,
  pending,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (instruction: string) => void;
  pending: boolean;
}) {
  const [instruction, setInstruction] = useState("");

  function handleSubmit() {
    const trimmed = instruction.trim();
    if (!trimmed || pending) return;
    onSubmit(trimmed);
  }

  function handleClose() {
    if (pending) return;
    setInstruction("");
    onClose();
  }

  if (!open) return null;

  return (
    <div className="dialog-overlay" data-testid="revision-dialog-overlay" onClick={handleClose}>
      <div className="dialog" data-testid="revision-dialog" onClick={(e) => e.stopPropagation()}>
        <h3 data-testid="revision-dialog-title">Request Revision</h3>
        <p className="meta">Describe what should change in the repair proposal.</p>
        <textarea
          data-testid="revision-instruction-input"
          className="revision-instruction-input"
          rows={4}
          placeholder="e.g. Only update validation dependency. Do not touch application.properties."
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          disabled={pending}
          maxLength={4000}
        />
        <p className="meta char-count">{instruction.length} / 4000</p>
        {instruction.trim().length === 0 && (
          <p className="meta warning-text" data-testid="revision-instruction-empty-warning">
            Instruction cannot be empty.
          </p>
        )}
        <div className="dialog-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={handleClose}
            disabled={pending}
            data-testid="revision-cancel-btn"
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleSubmit}
            disabled={pending || instruction.trim().length === 0}
            data-testid="revision-submit-btn"
          >
            {pending ? "Requesting revision..." : "Request Revision"}
          </button>
        </div>
      </div>
    </div>
  );
}
