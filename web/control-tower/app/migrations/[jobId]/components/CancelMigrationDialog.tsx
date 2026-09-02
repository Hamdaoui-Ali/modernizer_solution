"use client";

import { useEffect, useRef } from "react";

export function CancelMigrationDialog({
  open,
  cancelBusy,
  cancelError,
  onConfirm,
  onClose,
}: {
  open: boolean;
  cancelBusy: boolean;
  cancelError: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const keepRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => keepRef.current?.focus(), 50);
    return () => clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !cancelBusy) {
        onClose();
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, cancelBusy, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="cancel-dialog-title"
      style={{
        position: "fixed", inset: 0, zIndex: 130,
        display: "grid", placeItems: "center",
        padding: 20,
        background: "rgba(15,23,40,.52)",
        backdropFilter: "blur(5px)",
      }}
      onClick={(e) => { if (e.target === e.currentTarget && !cancelBusy) onClose(); }}
    >
      <div style={{
        width: "min(410px, 100%)", padding: 21,
        borderRadius: 14, background: "#fff",
        boxShadow: "0 28px 90px rgba(16,24,40,.3)",
        textAlign: "center",
      }}>
        <div style={{
          width: 44, height: 44, display: "grid", placeItems: "center",
          margin: "0 auto 11px", color: "#b42318",
          borderRadius: "50%", background: "#fff0f0",
          fontSize: 20, fontWeight: 900,
        }} aria-hidden="true">
          &#10005;
        </div>
        <h2 id="cancel-dialog-title" style={{
          margin: 0, color: "#0b0e14",
          fontSize: 17, letterSpacing: "-.02em",
        }}>
          Cancel this migration?
        </h2>
        <p style={{
          margin: "8px 0 15px", color: "#667085",
          fontSize: 10.4, lineHeight: 1.5,
        }}>
          This will stop the current migration and return you to the New Migration screen.
        </p>
        {cancelError && (
          <p role="alert" style={{
            margin: "0 0 12px", padding: "8px 10px",
            background: "#fff0f0", border: "1px solid #ffcaca",
            borderRadius: 8, color: "#b42318", fontSize: 10,
          }}>
            {cancelError}
          </p>
        )}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
          <button
            ref={keepRef}
            type="button"
            disabled={cancelBusy}
            onClick={onClose}
            style={{
              minHeight: 35, display: "inline-flex", alignItems: "center",
              justifyContent: "center", gap: 7, padding: "0 12px",
              color: "#172033", border: "1px solid #c7d0db",
              borderRadius: 10, background: "linear-gradient(180deg,#fff,#fbfcfe)",
              fontSize: 10.9, fontWeight: 720, cursor: "pointer",
            }}
          >
            Keep running
          </button>
          <button
            ref={confirmRef}
            type="button"
            disabled={cancelBusy}
            onClick={onConfirm}
            style={{
              minHeight: 35, display: "inline-flex", alignItems: "center",
              justifyContent: "center", gap: 7, padding: "0 12px",
              color: "#b42318", border: "1px solid #f6c9c6",
              borderRadius: 10, background: "#fff6f5",
              fontSize: 10.9, fontWeight: 720, cursor: "pointer",
            }}
          >
            {cancelBusy ? "Cancelling..." : "Confirm cancel"}
          </button>
        </div>
      </div>
    </div>
  );
}
