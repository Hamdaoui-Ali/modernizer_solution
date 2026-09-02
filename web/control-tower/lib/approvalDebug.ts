// Dev-only debug logging for the V2 approval flow.
//
// These helpers are gated on NODE_ENV so they are no-ops in production.
// They live in a lib module (not in the MigrationCockpit component) so the
// cockpit client bundle does not reference environment access directly.
//
// Only values already shown in the UI (checksums, card ids, stage indices)
// are logged. No secrets, tokens, or raw environment values are logged.

const isDev = process.env.NODE_ENV !== "production";

export function logApprovalEvent(event: unknown): void {
  if (isDev) {
    console.log("[approval-event]", event);
  }
}

export function logApprovalDecisionsBefore(approvals: unknown): void {
  if (isDev) {
    console.log("[approval-decisions-before]", approvals);
  }
}

export function logApprovalDecisionsAfter(approvals: unknown): void {
  if (isDev) {
    console.log("[approval-decisions-after]", approvals);
  }
}

export function logOpenGates(gates: unknown): void {
  if (isDev) {
    console.log("[open-gates]", gates);
  }
}

export function logApproveClickPayload(payload: unknown): void {
  if (isDev) {
    console.log("[approve-click-payload]", payload);
  }
}
