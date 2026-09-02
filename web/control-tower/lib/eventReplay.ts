import type { JobRepresentation, PublicRunEvent } from "./contracts";

export const STATE_CHANGING_EVENT_TYPES = new Set([
  "job_created",
  "job_state_changed",
  "command_queued",
  "artifact_registered"
]);

export type ReplayState = {
  events: PublicRunEvent[];
  lastAppliedSequence: number;
};

export function applyPublicEvent(state: ReplayState, event: PublicRunEvent): ReplayState {
  if (event.sequence <= state.lastAppliedSequence) {
    return state;
  }
  return {
    events: [...state.events, event],
    lastAppliedSequence: event.sequence
  };
}

export function shouldRefetchJobProjection(event: PublicRunEvent): boolean {
  return STATE_CHANGING_EVENT_TYPES.has(event.event_type);
}

export function latestAppliedSequence(events: PublicRunEvent[]): number {
  return events.reduce((latest, event) => Math.max(latest, event.sequence), 0);
}

export function jobStatusCopy(job: JobRepresentation["job"]): string {
  switch (job.state) {
    case "QUEUED":
      return "Command queued";
    case "STARTING":
      return "Foundation diagnostic starting";
    case "RUNNING":
      return "Foundation diagnostic running";
    case "CANCELLING":
      return "Foundation diagnostic cancelling";
    case "COMPLETED":
      return "Foundation diagnostic completed";
    case "FAILED":
      return "Foundation diagnostic failed";
    case "CANCELLED":
      return "Foundation diagnostic cancelled";
    case "RECOVERY_REQUIRED":
      return "Foundation diagnostic needs recovery";
    default:
      return "Foundation diagnostic job created";
  }
}
