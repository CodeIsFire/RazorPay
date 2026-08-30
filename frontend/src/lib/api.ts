import {
  Exception,
  ExceptionDetail,
  FunnelMetrics,
  DailyMetric,
  AnalyticsData,
  HealthStatus,
  IntegrationStatus,
  ChatMessage,
  ChatResponse,
  Action,
  ConfirmActionRequest,
  ResolveExceptionRequest,
  AuditEvent,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

class APIClient {
  private baseURL: string;

  constructor(baseURL: string = API_BASE) {
    this.baseURL = baseURL;
  }

  private async fetch<T>(
    path: string,
    options?: RequestInit
  ): Promise<T> {
    const url = `${this.baseURL}${path}`;
    const response = await fetch(url, {
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
      credentials: "include",
      ...options,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({
        message: response.statusText,
      }));
      throw new Error(`API Error: ${error.message || response.statusText}`);
    }

    return response.json() as Promise<T>;
  }

  // Health checks
  async health(): Promise<HealthStatus> {
    return this.fetch<HealthStatus>("/health");
  }

  async integrationStatus(): Promise<IntegrationStatus> {
    return this.fetch<IntegrationStatus>("/integration/status");
  }

  // Pipeline operations
  async reconcile(): Promise<{ status: string }> {
    return this.fetch<{ status: string }>("/pipeline/reconcile", {
      method: "POST",
    });
  }

  async syncPayouts(): Promise<{ status: string }> {
    return this.fetch<{ status: string }>("/pipeline/sync-payouts", {
      method: "POST",
    });
  }

  async routeExceptions(): Promise<{ status: string }> {
    return this.fetch<{ status: string }>("/pipeline/route", {
      method: "POST",
    });
  }

  // Analytics
  async getFunnel(): Promise<FunnelMetrics> {
    return this.fetch<FunnelMetrics>("/funnel");
  }

  async getDaily(): Promise<DailyMetric[]> {
    return this.fetch<DailyMetric[]>("/analytics/daily");
  }

  async getAnalytics(): Promise<AnalyticsData> {
    return this.fetch<AnalyticsData>("/analytics/exceptions");
  }

  // Exceptions
  async getExceptions(params?: {
    cause?: string;
    status?: string;
    limit?: number;
    offset?: number;
  }): Promise<{ exceptions: Exception[]; total: number }> {
    const query = new URLSearchParams();
    if (params?.cause) query.append("cause", params.cause);
    if (params?.status) query.append("status", params.status);
    if (params?.limit) query.append("limit", params.limit.toString());
    if (params?.offset) query.append("offset", params.offset.toString());

    const path = `/exceptions${query.toString() ? `?${query.toString()}` : ""}`;
    return this.fetch<{ exceptions: Exception[]; total: number }>(path);
  }

  async getExceptionDetail(exceptionKey: string): Promise<ExceptionDetail> {
    return this.fetch<ExceptionDetail>(
      `/exceptions/${encodeURIComponent(exceptionKey)}/detail`
    );
  }

  // Audit log
  async getAudit(params?: {
    exception_key?: string;
    limit?: number;
    offset?: number;
  }): Promise<{ events: AuditEvent[]; total: number }> {
    const query = new URLSearchParams();
    if (params?.exception_key)
      query.append("exception_key", params.exception_key);
    if (params?.limit) query.append("limit", params.limit.toString());
    if (params?.offset) query.append("offset", params.offset.toString());

    const path = `/audit${query.toString() ? `?${query.toString()}` : ""}`;
    return this.fetch<{ events: AuditEvent[]; total: number }>(path);
  }

  // Actions
  async confirmAction(
    actionId: string,
    request: ConfirmActionRequest
  ): Promise<Action> {
    return this.fetch<Action>(`/actions/${encodeURIComponent(actionId)}/confirm`, {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  async resolveException(
    exceptionKey: string,
    request: ResolveExceptionRequest
  ): Promise<Exception> {
    return this.fetch<Exception>(
      `/exceptions/${encodeURIComponent(exceptionKey)}/resolve`,
      {
        method: "POST",
        body: JSON.stringify(request),
      }
    );
  }

  async recheckException(exceptionKey: string): Promise<Exception> {
    return this.fetch<Exception>(
      `/exceptions/${encodeURIComponent(exceptionKey)}/recheck`,
      {
        method: "POST",
      }
    );
  }

  // Assistant
  async chat(messages: ChatMessage[]): Promise<ChatResponse> {
    return this.fetch<ChatResponse>("/assistant/chat", {
      method: "POST",
      body: JSON.stringify({ messages }),
    });
  }

  // Webhooks
  async handleWebhook(payload: unknown, signature: string): Promise<void> {
    return this.fetch<void>("/webhooks/razorpayx", {
      method: "POST",
      headers: {
        "x-razorpay-signature": signature,
      },
      body: JSON.stringify(payload),
    });
  }
}

export const apiClient = new APIClient();
