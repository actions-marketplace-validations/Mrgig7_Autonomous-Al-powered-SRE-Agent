/**
 * API client for SRE Agent Dashboard
 */

const API_BASE = '/api/v1';
const SESSION_HINT_STORAGE_KEY = 'sre_session_hint';

interface FetchOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  retryOnAuthFailure?: boolean;
}

interface ApiEnvelope<T> {
  success: boolean;
  data: T | null;
  error: {
    message: string;
    code?: string | null;
  } | null;
}

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export type GitHubLoginStartResponse = {
  authorization_url: string;
  state: string;
};

export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
};

export type UserRepository = {
  id: number;
  name: string;
  full_name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
  permissions: {
    admin: boolean;
    maintain: boolean;
    push: boolean;
    triage: boolean;
    pull: boolean;
  };
};

export type IntegrationInstallResponse = {
  repository: string;
  install_url: string;
  configured: boolean;
  provider: string;
  install_state: string;
  status: 'installing' | 'installed' | 'failed' | 'not_started';
};

export type InstallStatusResponse = {
  repository: string | null;
  status: 'installing' | 'installed' | 'failed' | 'not_started';
  installation_id: number | null;
};

export type OnboardingStatusResponse = {
  onboarding_status: {
    oauth_completed: boolean;
    repo_selected: boolean;
    app_installed: boolean;
    dashboard_ready: boolean;
  };
  selected_repository: string | null;
  installation_id: number | null;
};

export type FailureExplainResponse = {
  failure_id: string;
  repo: string;
  summary: {
    category?: string | null;
    root_cause?: string | null;
    adapter?: string | null;
    confidence: number;
    confidence_breakdown: Array<{
      factor: string;
      value: number;
      weight: number;
      note: string;
    }>;
  };
  evidence: Array<{
    idx: number;
    line: string;
    tag: string;
    operation_idx?: number | null;
  }>;
  proposed_fix: {
    plan?: any;
    files: string[];
    diff_available: boolean;
  };
  safety: any;
  validation: any;
  run: {
    run_id?: string | null;
    status?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
  };
  timeline: any[];
  generated_at: string;
};

export type RunDiffResponse = {
  run_id: string;
  diff_text: string;
  stats?: any;
  redacted: boolean;
};

export type RunTimelineResponse = {
  run_id: string;
  timeline: Array<{
    step: string;
    status: string;
    started_at?: string | null;
    completed_at?: string | null;
    duration_ms?: number | null;
  }>;
};

class ApiClient {
  private bearerToken: string | null = null;

  setToken(token: string) {
    this.bearerToken = token;
    this.setSessionHint(true);
  }

  clearToken() {
    this.bearerToken = null;
    this.setSessionHint(false);
  }

  hasSessionHint(): boolean {
    if (this.bearerToken) {
      return true;
    }

    const hint = this.getSessionHint();
    return hint === '1';
  }

  private getSessionHint(): string | null {
    if (typeof window === 'undefined') {
      return null;
    }

    try {
      return window.localStorage.getItem(SESSION_HINT_STORAGE_KEY);
    } catch {
      return null;
    }
  }

  private setSessionHint(hasSession: boolean): void {
    if (typeof window === 'undefined') {
      return;
    }

    try {
      window.localStorage.setItem(SESSION_HINT_STORAGE_KEY, hasSession ? '1' : '0');
    } catch {
      // localStorage can be blocked; auth flow still works without the hint.
    }
  }

  private async tryRefresh(): Promise<boolean> {
    const response = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify({}),
    });

    if (!response.ok) {
      return false;
    }

    const payload = (await response.json().catch(() => null)) as TokenResponse | null;
    if (payload?.access_token) {
      this.setToken(payload.access_token);
    }
    return true;
  }

  private unwrapEnvelope<T>(payload: T | ApiEnvelope<T>): T {
    if (typeof payload === 'object' && payload !== null && 'success' in payload && 'data' in payload) {
      const envelope = payload as ApiEnvelope<T>;
      if (!envelope.success) {
        const message = envelope.error?.message || 'Request failed';
        throw new ApiError(400, message);
      }
      return envelope.data as T;
    }
    return payload as T;
  }

  private async fetch<T>(endpoint: string, options: FetchOptions = {}): Promise<T> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Response-Envelope': 'true',
      ...options.headers,
    };

    if (this.bearerToken) {
      headers['Authorization'] = `Bearer ${this.bearerToken}`;
    }

    const response = await fetch(`${API_BASE}${endpoint}`, {
      method: options.method || 'GET',
      headers,
      credentials: 'include',
      body: options.body ? JSON.stringify(options.body) : undefined,
    });

    if (response.status === 401 && options.retryOnAuthFailure !== false) {
      const refreshed = await this.tryRefresh();
      if (refreshed) {
        return this.fetch<T>(endpoint, { ...options, retryOnAuthFailure: false });
      }
    }

    if (response.status === 401) {
      this.clearToken();
      window.dispatchEvent(new CustomEvent('sre-session-expired'));
      throw new ApiError(401, 'Session expired');
    }

    if (response.status === 204) {
      return undefined as T;
    }

    const payload = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = payload?.detail || payload?.error?.message || 'Request failed';
      throw new ApiError(response.status, detail);
    }

    return this.unwrapEnvelope<T>(payload as T | ApiEnvelope<T>);
  }

  // Auth
  async login(email: string, password: string) {
    const result = await this.fetch<TokenResponse>('/auth/login', {
      method: 'POST',
      body: { email, password },
      retryOnAuthFailure: false,
    });
    if (result.access_token) {
      this.setToken(result.access_token);
    }
    return result;
  }

  async startGitHubLogin() {
    return this.fetch<GitHubLoginStartResponse>('/auth/github/login', {
      method: 'POST',
      body: { action: 'start' },
      retryOnAuthFailure: false,
    });
  }

  async exchangeGitHubCode(code: string, state: string) {
    const result = await this.fetch<TokenResponse>('/auth/github/login', {
      method: 'POST',
      body: {
        action: 'exchange',
        code,
        state,
      },
      retryOnAuthFailure: false,
    });
    if (result.access_token) {
      this.setToken(result.access_token);
    }
    return result;
  }

  async logout() {
    await this.fetch('/auth/logout', { method: 'POST', retryOnAuthFailure: false }).catch(() => {});
    this.clearToken();
  }

  async getProfile() {
    const profile = await this.fetch<{
      id: string;
      email: string;
      name: string;
      role: string;
      permissions: string[];
    }>('/auth/me', { retryOnAuthFailure: false });
    this.setSessionHint(true);
    return profile;
  }

  // Dashboard
  async getOverview() {
    return this.fetch<{
      stats: {
        total_events: number;
        failures_24h: number;
        fixes_generated_24h: number;
        fixes_approved_24h: number;
        success_rate_7d: number;
        avg_fix_time_minutes: number;
      };
      recent_failures: Array<{
        id: string;
        repository: string;
        branch: string;
        status: string;
        ci_provider: string;
        created_at: string;
        error_snippet?: string;
      }>;
      pending_approvals: number;
      active_fixes: number;
    }>('/dashboard/overview');
  }

  async getEvents(params: {
    status?: string;
    repository?: string;
    limit?: number;
    offset?: number;
  } = {}) {
    const query = new URLSearchParams();
    if (params.status) query.set('status', params.status);
    if (params.repository) query.set('repository', params.repository);
    if (params.limit) query.set('limit', params.limit.toString());
    if (params.offset) query.set('offset', params.offset.toString());

    const queryStr = query.toString();
    return this.fetch<{
      events: Array<{
        id: string;
        repository: string;
        branch: string;
        status: string;
        ci_provider: string;
        created_at: string;
        error_snippet?: string;
      }>;
      total: number;
      limit: number;
      offset: number;
      has_more: boolean;
    }>(`/dashboard/events${queryStr ? `?${queryStr}` : ''}`);
  }

  async getTrends(days: number = 7) {
    return this.fetch<Array<{
      date: string;
      count: number;
      success_count: number;
      failure_count: number;
    }>>(`/dashboard/trends?days=${days}`);
  }

  async getRepoStats() {
    return this.fetch<Array<{
      repository: string;
      total_events: number;
      failures: number;
      success_rate: number;
      last_event_at?: string;
    }>>('/dashboard/repos');
  }

  async getUserRepos() {
    return this.fetch<UserRepository[]>('/user/repos');
  }

  async getIntegrationInstallLink(repository: string, automationMode: 'suggest' | 'auto_pr' | 'auto_merge') {
    return this.fetch<IntegrationInstallResponse>('/integration/install', {
      method: 'POST',
      body: { repository, automation_mode: automationMode },
    });
  }

  async confirmIntegrationInstall(state: string, installationId: number, setupAction?: string) {
    return this.fetch<InstallStatusResponse>('/integration/install/confirm', {
      method: 'POST',
      body: {
        state,
        installation_id: installationId,
        setup_action: setupAction,
      },
    });
  }

  async getIntegrationInstallStatus(repository?: string) {
    const suffix = repository ? `?repository=${encodeURIComponent(repository)}` : '';
    return this.fetch<InstallStatusResponse>(`/integration/install/status${suffix}`);
  }

  async getOnboardingStatus() {
    return this.fetch<OnboardingStatusResponse>('/integration/onboarding/status');
  }

  async getFailureExplain(failureId: string) {
    return this.fetch<FailureExplainResponse>(`/failures/${failureId}/explain`);
  }

  async getRunArtifact(runId: string) {
    return this.fetch<any>(`/runs/${runId}/artifact`);
  }

  async getRunDiff(runId: string) {
    return this.fetch<RunDiffResponse>(`/runs/${runId}/diff`);
  }

  async getRunTimeline(runId: string) {
    return this.fetch<RunTimelineResponse>(`/runs/${runId}/timeline`);
  }

  async getSystemHealth() {
    return this.fetch<{
      status: string;
      timestamp: string;
      components: Record<string, any>;
    }>('/dashboard/health');
  }

  // Notifications
  async listNotificationChannels() {
    return this.fetch<Array<{
      name: string;
      enabled: boolean;
      valid: boolean;
      type: string;
    }>>('/notifications/channels');
  }

  async testNotificationChannel(channelName: string) {
    return this.fetch(`/notifications/channels/${channelName}/test`, {
      method: 'POST',
    });
  }

  // Users
  async listUsers(params: { limit?: number; offset?: number; search?: string } = {}) {
    const query = new URLSearchParams();
    if (params.limit) query.set('limit', params.limit.toString());
    if (params.offset) query.set('offset', params.offset.toString());
    if (params.search) query.set('search', params.search);

    const queryStr = query.toString();
    return this.fetch<{
      users: Array<{
        id: string;
        email: string;
        name: string;
        role: string;
        is_active: boolean;
        created_at: string;
      }>;
      total: number;
    }>(`/users${queryStr ? `?${queryStr}` : ''}`);
  }

  // SSE Stream
  connectToEventStream(onMessage: (event: any) => void): EventSource {
    const eventSource = new EventSource(`${API_BASE}/dashboard/stream`);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        onMessage(data);
      } catch (e) {
        console.error('Failed to parse SSE message', e);
      }
    };

    eventSource.onerror = () => {
      console.error('SSE connection error');
    };

    return eventSource;
  }
}

export const api = new ApiClient();
export default api;
