"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { formatFinalDiffSource, type ReviewedDiffProposal } from "../../../lib/contracts";
import { useRepairAssistant } from "./useRepairAssistant";

interface RepairAssistantChatProps {
  jobId: string;
  proposal: ReviewedDiffProposal;
  proposalId: string;
  attemptNumber: number | null;
  reviewerDecision: string | null;
  finalDiffSource: string | null;
  diffChecksum: string;
  onNewProposal?: (newProposalId: string) => void;
  onRefreshProposal?: () => void;
}

const QUICK_ACTIONS = [
  "Explain this diff",
  "Why did this repair fail?",
  "Keep the dependency and change only its version",
  "Use the reviewer feedback",
  "Make the smallest possible change",
];

export function RepairAssistantChat({
  jobId,
  proposal,
  proposalId,
  attemptNumber,
  reviewerDecision,
  finalDiffSource,
  diffChecksum,
  onNewProposal,
  onRefreshProposal,
}: RepairAssistantChatProps) {
  const {
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
  } = useRepairAssistant({ jobId, proposalId, baseDiffChecksum: diffChecksum, onNewProposal, onRefreshProposal });

  const [inputText, setInputText] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = useCallback(async () => {
    const text = inputText.trim();
    if (!text || isSending) return;
    setInputText("");
    await sendMessage(text);
  }, [inputText, isSending, sendMessage]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void handleSend();
      }
    },
    [handleSend],
  );

  const handleQuickAction = useCallback(
    async (action: string) => {
      await sendMessage(action);
    },
    [sendMessage],
  );

  function formatTimestamp(ts: string): string {
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  }

  function renderContextBadges() {
    const badges: React.ReactNode[] = [];
    if (attemptNumber != null) {
      badges.push(
        <span key="attempt" className="rac-badge rac-badge-attempt">
          Attempt {attemptNumber}
        </span>,
      );
    }
    if (reviewerDecision) {
      const label = reviewerDecision.toUpperCase();
      let cls = "rac-badge-neutral";
      if (reviewerDecision === "accept") cls = "rac-badge-accept";
      else if (reviewerDecision === "revise") cls = "rac-badge-revise";
      else if (reviewerDecision === "reject") cls = "rac-badge-reject";
      badges.push(
        <span key="reviewer" className={`rac-badge ${cls}`}>
          {label}
        </span>,
      );
    }
    if (finalDiffSource) {
      badges.push(
        <span key="diff-source" className="rac-badge rac-badge-neutral">
          {formatFinalDiffSource(finalDiffSource)}
        </span>,
      );
    }
    return badges.length > 0 ? badges : null;
  }

  function getHeaderStatusLabel(): string {
    if (revisionStatus === "generating") return "Generating...";
    if (isSending) return "Sending...";
    if (isLoading) return "Loading...";
    return "Connected";
  }

  function getHeaderStatusClass(): string {
    if (revisionStatus === "generating" || isSending || isLoading) return "rac-status-busy";
    return "rac-status-ok";
  }

  const showWelcome = !isLoading && messages.length === 0 && !error;
  const disabled = isSending || revisionStatus === "generating";

  return (
    <div className="rac-container" data-testid="repair-assistant-chat">
      <style>{`
        .rac-container {
          background: #fff;
          border: 1px solid #e2e8f0;
          border-radius: 0.75rem;
          box-shadow: 0 1px 3px rgba(0,0,0,0.06);
          overflow: hidden;
          display: flex;
          flex-direction: column;
          font-size: 0.875rem;
          line-height: 1.5;
          color: #1e293b;
        }
        .rac-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0.75rem 1rem;
          border-bottom: 1px solid #f1f5f9;
          background: #fafbfc;
        }
        .rac-header-left {
          display: flex;
          align-items: center;
          gap: 0.5rem;
        }
        .rac-header-icon {
          width: 1.25rem;
          height: 1.25rem;
          color: #6366f1;
        }
        .rac-header-title {
          font-weight: 600;
          font-size: 0.9rem;
        }
        .rac-header-status {
          display: flex;
          align-items: center;
          gap: 0.35rem;
          font-size: 0.75rem;
          color: #64748b;
        }
        .rac-status-dot {
          width: 0.5rem;
          height: 0.5rem;
          border-radius: 50%;
          background: #22c55e;
        }
        .rac-status-dot.rac-status-busy {
          background: #f59e0b;
          animation: rac-pulse 1.5s infinite;
        }
        @keyframes rac-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        .rac-subtitle {
          padding: 0 1rem 0.5rem;
          font-size: 0.78rem;
          color: #94a3b8;
        }
        .rac-badges {
          display: flex;
          flex-wrap: wrap;
          gap: 0.4rem;
          padding: 0.25rem 1rem 0.5rem;
        }
        .rac-badge {
          display: inline-flex;
          align-items: center;
          font-size: 0.7rem;
          font-weight: 500;
          padding: 0.15rem 0.55rem;
          border-radius: 9999px;
          border: 1px solid #e2e8f0;
          background: #f8fafc;
          color: #475569;
          letter-spacing: 0.01em;
        }
        .rac-badge-attempt {
          background: #eef2ff;
          border-color: #c7d2fe;
          color: #4338ca;
        }
        .rac-badge-accept {
          background: #f0fdf4;
          border-color: #bbf7d0;
          color: #166534;
        }
        .rac-badge-revise {
          background: #fffbeb;
          border-color: #fde68a;
          color: #92400e;
        }
        .rac-badge-reject {
          background: #fef2f2;
          border-color: #fecaca;
          color: #991b1b;
        }
        .rac-badge-neutral {
          background: #f8fafc;
          border-color: #e2e8f0;
          color: #475569;
        }
        .rac-badge-info {
          background: #f0f9ff;
          border-color: #bae6fd;
          color: #075985;
        }
        .rac-messages {
          flex: 1;
          overflow-y: auto;
          padding: 0.75rem 1rem;
          display: flex;
          flex-direction: column;
          gap: 0.625rem;
          min-height: 12rem;
          max-height: 24rem;
          scrollbar-width: thin;
        }
        .rac-message {
          display: flex;
          flex-direction: column;
          max-width: 85%;
        }
        .rac-message-user {
          align-self: flex-end;
          align-items: flex-end;
        }
        .rac-message-assistant {
          align-self: flex-start;
          align-items: flex-start;
        }
        .rac-bubble {
          padding: 0.5rem 0.75rem;
          border-radius: 0.75rem;
          white-space: pre-wrap;
          word-wrap: break-word;
          overflow-wrap: break-word;
          line-height: 1.5;
        }
        .rac-bubble-user {
          background: #6366f1;
          color: #fff;
          border-bottom-right-radius: 0.2rem;
        }
        .rac-bubble-assistant {
          background: #f1f5f9;
          color: #1e293b;
          border-bottom-left-radius: 0.2rem;
          border: 1px solid #e2e8f0;
        }
        .rac-message-time {
          font-size: 0.65rem;
          color: #94a3b8;
          margin-top: 0.2rem;
          padding: 0 0.25rem;
        }
        .rac-welcome {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          text-align: center;
          padding: 2rem 1rem;
          color: #94a3b8;
          gap: 0.5rem;
          flex: 1;
        }
        .rac-welcome-icon {
          width: 2rem;
          height: 2rem;
          color: #c7d2fe;
        }
        .rac-welcome-text {
          font-size: 0.85rem;
          max-width: 22rem;
        }
        .rac-actions {
          padding: 0.5rem 1rem 0.25rem;
        }
        .rac-actions-label {
          font-size: 0.72rem;
          font-weight: 500;
          color: #94a3b8;
          margin-bottom: 0.35rem;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .rac-actions-grid {
          display: flex;
          flex-wrap: wrap;
          gap: 0.35rem;
        }
        .rac-action-chip {
          display: inline-flex;
          align-items: center;
          font-size: 0.75rem;
          padding: 0.25rem 0.65rem;
          border-radius: 9999px;
          border: 1px solid #e2e8f0;
          background: #f8fafc;
          color: #475569;
          cursor: pointer;
          transition: all 0.12s ease;
          white-space: nowrap;
        }
        .rac-action-chip:hover {
          background: #eef2ff;
          border-color: #c7d2fe;
          color: #4338ca;
        }
        .rac-action-chip:focus-visible {
          outline: 2px solid #6366f1;
          outline-offset: 2px;
        }
        .rac-action-chip:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .rac-input-area {
          display: flex;
          align-items: flex-end;
          gap: 0.5rem;
          padding: 0.5rem 1rem 0.75rem;
          border-top: 1px solid #f1f5f9;
        }
        .rac-input {
          flex: 1;
          resize: none;
          border: 1px solid #e2e8f0;
          border-radius: 0.5rem;
          padding: 0.5rem 0.75rem;
          font-size: 0.85rem;
          line-height: 1.4;
          font-family: inherit;
          min-height: 2.25rem;
          max-height: 6rem;
          transition: border-color 0.12s;
        }
        .rac-input:focus {
          outline: none;
          border-color: #6366f1;
          box-shadow: 0 0 0 2px rgba(99,102,241,0.15);
        }
        .rac-input:disabled {
          background: #f8fafc;
          color: #94a3b8;
          cursor: not-allowed;
        }
        .rac-send-btn {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 2.25rem;
          height: 2.25rem;
          border-radius: 0.5rem;
          border: 1px solid #6366f1;
          background: #6366f1;
          color: #fff;
          cursor: pointer;
          transition: all 0.12s;
          flex-shrink: 0;
        }
        .rac-send-btn:hover:not(:disabled) {
          background: #4f46e5;
          border-color: #4f46e5;
        }
        .rac-send-btn:disabled {
          background: #c7d2fe;
          border-color: #c7d2fe;
          cursor: not-allowed;
        }
        .rac-send-btn:focus-visible {
          outline: 2px solid #6366f1;
          outline-offset: 2px;
        }
        .rac-send-icon {
          width: 1rem;
          height: 1rem;
        }
        .rac-error {
          margin: 0.5rem 1rem;
          padding: 0.5rem 0.75rem;
          background: #fef2f2;
          border: 1px solid #fecaca;
          border-radius: 0.5rem;
          color: #991b1b;
          font-size: 0.8rem;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 0.5rem;
        }
        .rac-error-close {
          background: none;
          border: none;
          color: #991b1b;
          cursor: pointer;
          font-size: 1rem;
          padding: 0;
          line-height: 1;
          flex-shrink: 0;
        }
        .rac-revision-status {
          margin: 0.5rem 1rem;
          padding: 0.75rem;
          border-radius: 0.5rem;
          font-size: 0.8rem;
          display: flex;
          align-items: center;
          gap: 0.5rem;
        }
        .rac-revision-generating {
          background: #fffbeb;
          border: 1px solid #fde68a;
          color: #92400e;
        }
        .rac-revision-created {
          background: #f0fdf4;
          border: 1px solid #bbf7d0;
          color: #166534;
        }
        .rac-spinner {
          width: 1rem;
          height: 1rem;
          border: 2px solid #e2e8f0;
          border-top-color: #6366f1;
          border-radius: 50%;
          animation: rac-spin 0.6s linear infinite;
          flex-shrink: 0;
        }
        @keyframes rac-spin {
          to { transform: rotate(360deg); }
        }
        .rac-loading-dots {
          display: inline-flex;
          gap: 0.2rem;
          align-items: center;
          padding: 0.25rem 0;
        }
        .rac-loading-dots span {
          width: 0.35rem;
          height: 0.35rem;
          border-radius: 50%;
          background: #6366f1;
          animation: rac-dot-pulse 1.2s infinite;
        }
        .rac-loading-dots span:nth-child(2) {
          animation-delay: 0.2s;
        }
        .rac-loading-dots span:nth-child(3) {
          animation-delay: 0.4s;
        }
        @keyframes rac-dot-pulse {
          0%, 80%, 100% { opacity: 0.3; }
          40% { opacity: 1; }
        }
        .rac-revision-id {
          font-weight: 600;
          font-family: monospace;
          color: #166534;
        }
        .rac-failure-diagnostic {
          margin: 0.5rem 1rem;
          padding: 0.75rem;
          border-radius: 0.5rem;
          background: #fef2f2;
          border: 1px solid #fecaca;
          font-size: 0.8rem;
        }
        .rac-failure-header {
          display: flex;
          align-items: center;
          gap: 0.35rem;
          font-weight: 600;
          color: #991b1b;
          margin-bottom: 0.5rem;
        }
        .rac-failure-body {
          display: flex;
          flex-wrap: wrap;
          gap: 0.5rem;
          margin-bottom: 0.5rem;
        }
        .rac-failure-label,
        .rac-failure-code,
        .rac-failure-correlation {
          font-size: 0.78rem;
          color: #7f1d1d;
          background: #fee2e2;
          padding: 0.15rem 0.5rem;
          border-radius: 0.3rem;
        }
        .rac-failure-message {
          margin: 0.25rem 0 0;
          font-size: 0.78rem;
          color: #7f1d1d;
          width: 100%;
        }
        .rac-failure-unaffected {
          margin: 0.25rem 0 0;
          font-size: 0.78rem;
          color: #92400e;
          font-style: italic;
          font-weight: 500;
        }
      `}</style>

      <div className="rac-header">
        <div className="rac-header-left">
          <svg className="rac-header-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2a10 10 0 1 0 10 10" />
            <path d="M12 6v6l4 2" />
            <path d="M22 12h-2" />
            <path d="M22 2v8h-8" />
          </svg>
          <span className="rac-header-title">Repair Assistant</span>
        </div>
        <div className="rac-header-status">
          <span className={`rac-status-dot ${getHeaderStatusClass()}`} />
          {getHeaderStatusLabel()}
        </div>
      </div>

      <div className="rac-subtitle">Understand and revise this repair proposal</div>

      {renderContextBadges() && <div className="rac-badges">{renderContextBadges()}</div>}

      {error && (
        <div className="rac-error" role="alert">
          <span>{error}</span>
          <button type="button" className="rac-error-close" onClick={clearError} aria-label="Dismiss">&times;</button>
        </div>
      )}

      {revisionStatus === "generating" && (
        <div className="rac-revision-status rac-revision-generating">
          <div className="rac-spinner" />
          Generating revised proposal...
        </div>
      )}

      {revisionStatus === "created" && newProposalId && (
        <div className="rac-revision-status rac-revision-created">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M20 6L9 17l-5-5" />
          </svg>
          <span>Revised proposal created: <span className="rac-revision-id">{newProposalId}</span></span>
        </div>
      )}

      {failureStage && (
        <div className="rac-failure-diagnostic" data-testid="rac-failure-diagnostic">
          <div className="rac-failure-header">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <span>Revision failed</span>
          </div>
          <div className="rac-failure-body">
            <span className="rac-failure-label">Stage: <strong>{failureStage}</strong></span>
            {failureCode && <span className="rac-failure-code">Code: <strong>{failureCode}</strong></span>}
            {correlationId && <span className="rac-failure-correlation">ID: <strong>{correlationId}</strong></span>}
            {safeFailureMessage && <p className="rac-failure-message">{safeFailureMessage}</p>}
          </div>
          <p className="rac-failure-unaffected">The current proposal remains unchanged.</p>
        </div>
      )}

      {revisionStatus === "created" && !newProposalId && failureStage === null && (
        <div className="rac-revision-status rac-revision-created">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M20 6L9 17l-5-5" />
          </svg>
          <span>Revised proposal created</span>
        </div>
      )}

      <div className="rac-messages" data-testid="rac-messages">
        {isLoading && messages.length === 0 && (
          <div className="rac-welcome">
            <div className="rac-loading-dots"><span /><span /><span /></div>
            <div className="rac-welcome-text">Loading conversation history...</div>
          </div>
        )}

        {showWelcome && (
          <div className="rac-welcome">
            <svg className="rac-welcome-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <div className="rac-welcome-text">
              Ask questions about this repair proposal or request changes. The assistant understands the diff, reviewer feedback, and repository context.
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.message_id}
            className={`rac-message ${msg.role === "user" ? "rac-message-user" : "rac-message-assistant"}`}
          >
            <div className={`rac-bubble ${msg.role === "user" ? "rac-bubble-user" : "rac-bubble-assistant"}`}>
              {msg.message}
            </div>
            <span className="rac-message-time">{formatTimestamp(msg.created_at)}</span>
          </div>
        ))}

        {isSending && (
          <div className="rac-message rac-message-user">
            <div className="rac-bubble rac-bubble-user">
              <div className="rac-loading-dots"><span /><span /><span /></div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <div className="rac-actions">
        <div className="rac-actions-label">Quick Actions</div>
        <div className="rac-actions-grid">
          {QUICK_ACTIONS.map((action) => (
            <button
              key={action}
              type="button"
              className="rac-action-chip"
              disabled={disabled}
              onClick={() => void handleQuickAction(action)}
            >
              {action}
            </button>
          ))}
        </div>
      </div>

      <div className="rac-input-area">
        <textarea
          ref={inputRef}
          className="rac-input"
          rows={1}
          placeholder="Ask about or revise this repair..."
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          aria-label="Ask about or revise this repair"
        />
        <button
          type="button"
          className="rac-send-btn"
          disabled={disabled || !inputText.trim()}
          onClick={() => void handleSend()}
          aria-label="Send message"
        >
          <svg className="rac-send-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
    </div>
  );
}
