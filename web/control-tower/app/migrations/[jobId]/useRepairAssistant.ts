"use client";
import { useState, useCallback, useEffect, useRef } from "react";
import {
  fetchRepairAssistantMessages,
  getRepairProposal,
  getRepairProposalDiff,
  sendRepairAssistantMessage,
} from "../../../lib/controlTowerApi";
import type {
  RepairAssistantMessage,
  RepairAssistantMessageStatus,
} from "../../../lib/contracts";

interface UseRepairAssistantOptions {
  jobId: string;
  proposalId: string;
  baseDiffChecksum: string;
  onNewProposal?: (newProposalId: string) => void;
  onRefreshProposal?: () => void;
}

export function useRepairAssistant({
  jobId,
  proposalId,
  baseDiffChecksum,
  onNewProposal,
  onRefreshProposal,
}: UseRepairAssistantOptions) {
  const [messages, setMessages] = useState<RepairAssistantMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revisionStatus, setRevisionStatus] = useState<"idle" | "generating" | "created" | null>(null);
  const [newProposalId, setNewProposalId] = useState<string | null>(null);
  const [failureStage, setFailureStage] = useState<string | null>(null);
  const [failureCode, setFailureCode] = useState<string | null>(null);
  const [correlationId, setCorrelationId] = useState<string | null>(null);
  const [safeFailureMessage, setSafeFailureMessage] = useState<string | null>(null);

  const lastProposalRef = useRef(proposalId);
  const pendingRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const loadMessages = useCallback(async () => {
    if (!jobId || !proposalId) return;
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetchRepairAssistantMessages(jobId, proposalId);
      if (mountedRef.current) {
        setMessages(response.messages ?? []);
      }
    } catch (err) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : "Failed to load messages");
        setMessages([]);
      }
    } finally {
      if (mountedRef.current) {
        setIsLoading(false);
      }
    }
  }, [jobId, proposalId]);

  useEffect(() => {
    if (lastProposalRef.current !== proposalId) {
      lastProposalRef.current = proposalId;
      setMessages([]);
      setError(null);
      setRevisionStatus(null);
      setNewProposalId(null);
      setFailureStage(null);
      setFailureCode(null);
      setCorrelationId(null);
      setSafeFailureMessage(null);
      loadMessages();
    }
  }, [proposalId, loadMessages]);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim()) return;
    if (pendingRef.current) return;
    pendingRef.current = true;
    setIsSending(true);
    setError(null);

    const tempUserMessage: RepairAssistantMessage = {
      message_id: `temp-${Date.now()}`,
      job_id: jobId,
      proposal_id: proposalId,
      role: "user",
      message: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMessage]);

    try {
      const idempotencyKey = globalThis.crypto?.randomUUID?.() ??
        `${"xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx"}`.replace(/[xy]/g, (c) => {
          const r = Math.random() * 16 | 0;
          return (c === "x" ? r : (r & 0x3 | 0x8)).toString(16);
        });

      const response = await sendRepairAssistantMessage(jobId, proposalId, {
        message: text,
        idempotency_key: idempotencyKey,
        base_diff_checksum: baseDiffChecksum,
      });

      if (response.status === "revision_failed") {
        if (mountedRef.current) {
          setFailureStage(response.failure_stage ?? null);
          setFailureCode(response.failure_code ?? null);
          setCorrelationId(response.correlation_id ?? null);
          setSafeFailureMessage(response.safe_failure_message ?? null);
          setError(`Revision failed (${response.failure_stage ?? "generation"}/${response.failure_code ?? "unknown"}).`);
          setRevisionStatus(null);
        }
        await loadMessages();
        return;
      }

      if (response.revision_started && response.status === "revision_generating") {
        if (mountedRef.current) setRevisionStatus("generating");
      }

      const newId = response.new_proposal_id;
      if (response.status === "revision_created" || response.revision_started) {
        if (mountedRef.current) {
          setRevisionStatus(response.status === "revision_created" ? "created" : "generating");
        }
        if (newId && newId !== proposalId) {
          if (mountedRef.current) setNewProposalId(newId);
          await onNewProposal?.(newId);
          onRefreshProposal?.();
        }
      }

      await loadMessages();
    } catch (err) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : "Failed to send message");
      }
    } finally {
      if (mountedRef.current) {
        setIsSending(false);
      }
      pendingRef.current = false;
    }
  }, [jobId, proposalId, baseDiffChecksum, loadMessages, onNewProposal, onRefreshProposal]);

  const reloadMessages = useCallback(async () => {
    await loadMessages();
  }, [loadMessages]);

  const clearError = useCallback(() => {
    if (mountedRef.current) setError(null);
  }, []);

  return {
    messages,
    isLoading,
    isSending,
    error,
    revisionStatus,
    newProposalId,
    failureStage,
    failureCode,
    correlationId,
    safeFailureMessage,
    sendMessage,
    reloadMessages,
    clearError,
  } as const;
}
