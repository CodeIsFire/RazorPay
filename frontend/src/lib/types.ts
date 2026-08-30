// API Response Types

export type CauseType =
  | "failed_payment"
  | "fee_mismatch"
  | "timing_lag"
  | "duplicate"
  | "unexplained"
  | "refund_unmatched"
  | "chargeback"
  | "partial_payment";

export type ExceptionStatus = "open" | "pending" | "resolved" | "abandoned";

export type ActionType =
  | "retry_payout"
  | "draft_dispute_note"
  | "send_reminder"
  | "flag_for_review";

export interface Exception {
  exception_key: string;
  cause: CauseType;
  status: ExceptionStatus;
  ledger_amount: number;
  gateway_amount: number;
  bank_amount: number;
  counterparty: string;
  created_at: string;
  created_age_seconds: number;
  latest_action: {
    action_id: string;
    type: ActionType;
    status: "queued" | "processing" | "succeeded" | "error";
  } | null;
}

export interface ExceptionDetail extends Exception {
  ledger_ref: string[];
  gateway_ref: string | null;
  bank_ref: string | null;
  audit_log: AuditEvent[];
}

export interface AuditEvent {
  created_at: string;
  exception_key: string;
  event: string;
  details: Record<string, unknown>;
}

export interface FunnelMetrics {
  ledger_total: number;
  ledger_matched: number;
  gateway_total: number;
  gateway_matched: number;
  bank_total: number;
  bank_matched: number;
  settled_amount: number;
  gap_amount: number;
  gap_percentage: number;
}

export interface DailyMetric {
  date: string;
  reconciled_amount: number;
  outstanding_amount: number;
  exception_count: number;
}

export interface AnalyticsData {
  by_cause: Record<
    CauseType,
    {
      count: number;
      total_amount: number;
      avg_amount: number;
    }
  >;
  by_age: Record<
    string,
    {
      count: number;
      total_amount: number;
    }
  >;
  by_counterparty: Record<
    string,
    {
      count: number;
      total_amount: number;
    }
  >;
}

export interface HealthStatus {
  status: "ok" | "error";
  message: string;
}

export interface IntegrationStatus {
  razorpayx_connected: boolean;
  razorpayx_error: string | null;
  last_sync: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatResponse {
  message: string;
  rate_limited: boolean;
  rate_limit_reset_seconds: number | null;
}

export interface Action {
  action_id: string;
  exception_key: string;
  type: ActionType;
  status: "queued" | "processing" | "succeeded" | "error";
  created_at: string;
  updated_at: string;
  retry_count?: number;
  error_message?: string;
}

export interface ConfirmActionRequest {
  decision: "succeeded" | "reversed" | "abandoned";
  notes?: string;
}

export interface ResolveExceptionRequest {
  decision: string;
  notes?: string;
}
