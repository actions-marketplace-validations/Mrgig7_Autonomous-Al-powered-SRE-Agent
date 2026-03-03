import { useCallback, useEffect, useState } from 'react';
import { Link, Route, Routes, useLocation } from 'react-router-dom';
import { format } from 'date-fns';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { User } from '../App';
import api, { type UserRepository } from '../api/client';
import FailureDetails from './FailureDetails';

interface DashboardProps {
  user: User;
  onLogout: () => void;
}

interface Overview {
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
}

type AutomationMode = 'suggest' | 'auto_pr' | 'auto_merge';
type InstallUiStatus = 'idle' | 'installing' | 'installed' | 'failed';

export default function Dashboard({ user, onLogout }: DashboardProps) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [trends, setTrends] = useState<any[]>([]);
  const [repoStats, setRepoStats] = useState<any[]>([]);
  const [repositories, setRepositories] = useState<UserRepository[]>([]);
  const [selectedRepository, setSelectedRepository] = useState('');
  const [automationMode, setAutomationMode] = useState<AutomationMode>('suggest');
  const [reposLoading, setReposLoading] = useState(true);
  const [onboardingError, setOnboardingError] = useState('');
  const [installLoading, setInstallLoading] = useState(false);
  const [installUrl, setInstallUrl] = useState('');
  const [installStatus, setInstallStatus] = useState<InstallUiStatus>('idle');
  const [installStatusMessage, setInstallStatusMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [sseConnected, setSseConnected] = useState(false);
  const location = useLocation();

  const fetchData = useCallback(async () => {
    try {
      const [overviewData, trendsData, repoData] = await Promise.all([
        api.getOverview(),
        api.getTrends(7),
        api.getRepoStats(),
      ]);
      setOverview(overviewData);
      setTrends(trendsData);
      setRepoStats(repoData);
    } catch (error) {
      console.error('Failed to fetch dashboard data:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchRepositories = useCallback(async () => {
    try {
      const [repoList, onboarding] = await Promise.all([
        api.getUserRepos(),
        api.getOnboardingStatus().catch(() => null),
      ]);
      setRepositories(repoList);
      if (
        onboarding?.selected_repository &&
        repoList.some((repo) => repo.full_name === onboarding.selected_repository)
      ) {
        setSelectedRepository(onboarding.selected_repository);
      } else if (!selectedRepository && repoList.length > 0) {
        setSelectedRepository(repoList[0].full_name);
      }
      if (onboarding?.onboarding_status?.app_installed) {
        setInstallStatus('installed');
        setInstallStatusMessage('Installed ✓');
      }
      setOnboardingError('');
    } catch (error: any) {
      const message = error?.message || 'Failed to load GitHub repositories.';
      setOnboardingError(message);
      setRepositories([]);
    } finally {
      setReposLoading(false);
    }
  }, [selectedRepository]);

  const fetchInstallStatus = useCallback(async (repository: string) => {
    if (!repository) {
      return;
    }
    try {
      const status = await api.getIntegrationInstallStatus(repository);
      if (status.status === 'installed') {
        setInstallStatus('installed');
        setInstallStatusMessage('Installed ✓');
      } else if (status.status === 'installing') {
        setInstallStatus('installing');
        setInstallStatusMessage('Installing...');
      } else {
        setInstallStatus('idle');
        setInstallStatusMessage('');
      }
    } catch {
      setInstallStatus('failed');
      setInstallStatusMessage('Failed ❌');
    }
  }, []);

  useEffect(() => {
    fetchData();
    fetchRepositories();

    const eventSource = api.connectToEventStream((event) => {
      if (event.type === 'connected') {
        setSseConnected(true);
      } else if (event.type !== 'heartbeat') {
        fetchData();
      }
    });

    return () => {
      eventSource.close();
      setSseConnected(false);
    };
  }, [fetchData, fetchRepositories]);

  useEffect(() => {
    if (!selectedRepository) {
      return;
    }
    fetchInstallStatus(selectedRepository);
  }, [fetchInstallStatus, selectedRepository]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const install = params.get('install');
    if (!install) {
      return;
    }
    if (install === 'installed') {
      setInstallStatus('installed');
      setInstallStatusMessage('Installed ✓');
    } else if (install === 'installing') {
      setInstallStatus('installing');
      setInstallStatusMessage('Installing...');
    } else if (install === 'failed') {
      setInstallStatus('failed');
      setInstallStatusMessage('Failed ❌');
    }

    const repo = params.get('repo');
    if (repo) {
      setSelectedRepository(repo);
    }
  }, [location.search]);

  const handleInstallClick = async () => {
    if (!selectedRepository) return;

    setInstallLoading(true);
    setOnboardingError('');
    setInstallStatus('installing');
    setInstallStatusMessage('Installing...');
    try {
      const response = await api.getIntegrationInstallLink(selectedRepository, automationMode);
      setInstallUrl(response.install_url);
      window.open(response.install_url, '_blank', 'noopener,noreferrer');
    } catch (error: any) {
      setInstallStatus('failed');
      setInstallStatusMessage('Failed ❌');
      setOnboardingError(error?.message || 'Failed to create GitHub App install link.');
    } finally {
      setInstallLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="loading-screen">
        <div className="loading-spinner"></div>
      </div>
    );
  }

  return (
    <div className="dashboard">
      <nav className="sidebar">
        <div className="sidebar-header">
          <h1>SRE Agent</h1>
          <span className={`connection-status ${sseConnected ? 'connected' : ''}`}>
            {sseConnected ? 'Live' : 'Offline'}
          </span>
        </div>

        <ul className="nav-links">
          <li className={location.pathname === '/app' ? 'active' : ''}>
            <Link to="/app">Dashboard</Link>
          </li>
          <li className={location.pathname.startsWith('/app/events') ? 'active' : ''}>
            <Link to="/app/events">Events</Link>
          </li>
          <li className={location.pathname.startsWith('/app/approvals') ? 'active' : ''}>
            <Link to="/app/approvals">Approvals</Link>
          </li>
          <li className={location.pathname.startsWith('/app/notifications') ? 'active' : ''}>
            <Link to="/app/notifications">Notifications</Link>
          </li>
          {user.permissions.includes('view_users') && (
            <li className={location.pathname.startsWith('/app/users') ? 'active' : ''}>
              <Link to="/app/users">Users</Link>
            </li>
          )}
        </ul>

        <div className="sidebar-footer">
          <div className="user-info">
            <span className="user-name">{user.name}</span>
            <span className="user-role">{user.role}</span>
          </div>
          <button onClick={onLogout} className="logout-btn">
            Logout
          </button>
        </div>
      </nav>

      <main className="main-content">
        <Routes>
          <Route
            index
            element={
              <OverviewPage
                overview={overview}
                trends={trends}
                repoStats={repoStats}
                repositories={repositories}
                reposLoading={reposLoading}
                selectedRepository={selectedRepository}
                onSelectedRepositoryChange={setSelectedRepository}
                automationMode={automationMode}
                onAutomationModeChange={setAutomationMode}
                onboardingError={onboardingError}
                onInstall={handleInstallClick}
                installLoading={installLoading}
                installUrl={installUrl}
                installStatus={installStatus}
                installStatusMessage={installStatusMessage}
              />
            }
          />
          <Route path="events" element={<EventsPage />} />
          <Route path="failures/:failureId" element={<FailureDetails />} />
          <Route path="approvals" element={<ApprovalsPage />} />
          <Route path="notifications" element={<NotificationsPage />} />
          <Route path="users" element={<UsersPage />} />
        </Routes>
      </main>
    </div>
  );
}

interface OverviewPageProps {
  overview: Overview | null;
  trends: any[];
  repoStats: any[];
  repositories: UserRepository[];
  reposLoading: boolean;
  selectedRepository: string;
  onSelectedRepositoryChange: (value: string) => void;
  automationMode: AutomationMode;
  onAutomationModeChange: (mode: AutomationMode) => void;
  onboardingError: string;
  onInstall: () => Promise<void>;
  installLoading: boolean;
  installUrl: string;
  installStatus: InstallUiStatus;
  installStatusMessage: string;
}

function OverviewPage({
  overview,
  trends,
  repoStats,
  repositories,
  reposLoading,
  selectedRepository,
  onSelectedRepositoryChange,
  automationMode,
  onAutomationModeChange,
  onboardingError,
  onInstall,
  installLoading,
  installUrl,
  installStatus,
  installStatusMessage,
}: OverviewPageProps) {
  if (!overview) return null;

  const { stats, recent_failures } = overview;

  return (
    <div className="page overview-page">
      <h2>Dashboard Overview</h2>

      <div className="onboarding-card">
        <h3>Phase 1 Onboarding</h3>
        <p>Connect repository access, install GitHub App, and choose automation mode.</p>
        <div className="onboarding-grid">
          <label className="onboarding-field">
            <span>Repository Selector</span>
            <select
              value={selectedRepository}
              onChange={(e) => onSelectedRepositoryChange(e.target.value)}
              disabled={reposLoading || repositories.length === 0}
            >
              {repositories.length === 0 && <option value="">No repositories available</option>}
              {repositories.map((repo) => (
                <option key={repo.id} value={repo.full_name}>
                  {repo.full_name}
                </option>
              ))}
            </select>
          </label>

          <label className="onboarding-field">
            <span>Automation Mode</span>
            <select
              value={automationMode}
              onChange={(e) => onAutomationModeChange(e.target.value as AutomationMode)}
            >
              <option value="suggest">suggest</option>
              <option value="auto_pr">auto_pr</option>
              <option value="auto_merge">auto_merge</option>
            </select>
          </label>

          <button
            className="install-button"
            disabled={!selectedRepository || installLoading}
            onClick={onInstall}
          >
            {installLoading || installStatus === 'installing'
              ? 'Installing...'
              : installStatus === 'installed'
                ? 'Installed ✓'
                : installStatus === 'failed'
                  ? 'Failed ❌'
                  : 'Install GitHub App'}
          </button>
        </div>
        {installStatusMessage && <p className="muted">Install status: {installStatusMessage}</p>}
        {installUrl && (
          <p className="muted">
            Install link generated:{' '}
            <a href={installUrl} target="_blank" rel="noreferrer" className="link">
              Open in GitHub
            </a>
          </p>
        )}
        {onboardingError && <div className="error-message">{onboardingError}</div>}
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <span className="stat-value">{stats.total_events}</span>
          <span className="stat-label">Total Events</span>
        </div>
        <div className="stat-card warning">
          <span className="stat-value">{stats.failures_24h}</span>
          <span className="stat-label">Failures (24h)</span>
        </div>
        <div className="stat-card success">
          <span className="stat-value">{stats.fixes_generated_24h}</span>
          <span className="stat-label">Fixes Generated</span>
        </div>
        <div className="stat-card">
          <span className="stat-value">{stats.success_rate_7d}%</span>
          <span className="stat-label">Success Rate (7d)</span>
        </div>
      </div>

      <div className="charts-row">
        <div className="chart-card">
          <h3>Event Trends (7 Days)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={trends}>
              <CartesianGrid strokeDasharray="3 3" stroke="#333" />
              <XAxis dataKey="date" stroke="#888" />
              <YAxis stroke="#888" />
              <Tooltip contentStyle={{ background: '#1a1a2e', border: '1px solid #333' }} />
              <Line type="monotone" dataKey="count" stroke="#8b5cf6" strokeWidth={2} />
              <Line type="monotone" dataKey="failure_count" stroke="#ef4444" strokeWidth={2} />
              <Line type="monotone" dataKey="success_count" stroke="#22c55e" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-card">
          <h3>Top Repositories</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={repoStats.slice(0, 5)} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="#333" />
              <XAxis type="number" stroke="#888" />
              <YAxis dataKey="repository" type="category" stroke="#888" width={100} />
              <Tooltip contentStyle={{ background: '#1a1a2e', border: '1px solid #333' }} />
              <Bar dataKey="total_events" fill="#8b5cf6" />
              <Bar dataKey="failures" fill="#ef4444" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="table-card">
        <h3>Recent Failures</h3>
        <table>
          <thead>
            <tr>
              <th>Repository</th>
              <th>Branch</th>
              <th>CI Provider</th>
              <th>Time</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {recent_failures.map((event) => (
              <tr key={event.id}>
                <td>
                  <Link to={`/app/failures/${event.id}`} className="link">
                    {event.repository}
                  </Link>
                </td>
                <td>{event.branch}</td>
                <td>{event.ci_provider}</td>
                <td>{format(new Date(event.created_at), 'MMM d, HH:mm')}</td>
                <td>
                  <span className={`status-badge ${event.status}`}>{event.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EventsPage() {
  const [events, setEvents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const limit = 20;

  useEffect(() => {
    const fetchEvents = async () => {
      setLoading(true);
      const data = await api.getEvents({ limit, offset: page * limit });
      setEvents(data.events);
      setTotal(data.total);
      setLoading(false);
    };
    fetchEvents();
  }, [page]);

  return (
    <div className="page events-page">
      <h2>Pipeline Events</h2>

      <div className="table-card">
        {loading ? (
          <div className="loading-spinner"></div>
        ) : (
          <>
            <table>
              <thead>
                <tr>
                  <th>Repository</th>
                  <th>Branch</th>
                  <th>CI Provider</th>
                  <th>Time</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id}>
                    <td>
                      <Link to={`/app/failures/${event.id}`} className="link">
                        {event.repository}
                      </Link>
                    </td>
                    <td>{event.branch}</td>
                    <td>{event.ci_provider}</td>
                    <td>{format(new Date(event.created_at), 'MMM d, HH:mm')}</td>
                    <td>
                      <span className={`status-badge ${event.status}`}>{event.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="pagination">
              <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                Previous
              </button>
              <span>
                Page {page + 1} of {Math.ceil(total / limit)}
              </span>
              <button disabled={(page + 1) * limit >= total} onClick={() => setPage((p) => p + 1)}>
                Next
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ApprovalsPage() {
  return (
    <div className="page">
      <h2>Pending Approvals</h2>
      <div className="empty-state">
        <span className="empty-icon">Approval</span>
        <p>No pending approvals</p>
      </div>
    </div>
  );
}

function NotificationsPage() {
  const [channels, setChannels] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchChannels = async () => {
      const data = await api.listNotificationChannels();
      setChannels(data);
      setLoading(false);
    };
    fetchChannels();
  }, []);

  const testChannel = async (name: string) => {
    try {
      await api.testNotificationChannel(name);
      alert('Test notification sent!');
    } catch (err: any) {
      alert('Failed to send test: ' + err.message);
    }
  };

  return (
    <div className="page">
      <h2>Notification Channels</h2>

      <div className="channels-grid">
        {loading ? (
          <div className="loading-spinner"></div>
        ) : channels.length === 0 ? (
          <div className="empty-state">
            <span className="empty-icon">Notifications</span>
            <p>No notification channels configured</p>
          </div>
        ) : (
          channels.map((channel) => (
            <div key={channel.name} className="channel-card">
              <div className="channel-header">
                <span className="channel-name">{channel.name}</span>
                <span className={`channel-status ${channel.enabled ? 'enabled' : 'disabled'}`}>
                  {channel.enabled ? 'Enabled' : 'Disabled'}
                </span>
              </div>
              <div className="channel-type">{channel.type}</div>
              <div className="channel-valid">{channel.valid ? 'Valid config' : 'Invalid config'}</div>
              <button onClick={() => testChannel(channel.name)} className="test-btn">
                Send Test
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function UsersPage() {
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  useEffect(() => {
    const fetchUsers = async () => {
      setLoading(true);
      const data = await api.listUsers({ search: search || undefined });
      setUsers(data.users);
      setLoading(false);
    };
    fetchUsers();
  }, [search]);

  return (
    <div className="page">
      <h2>User Management</h2>

      <div className="search-bar">
        <input
          type="text"
          placeholder="Search users..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="table-card">
        {loading ? (
          <div className="loading-spinner"></div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((currentUser) => (
                <tr key={currentUser.id}>
                  <td>{currentUser.name}</td>
                  <td>{currentUser.email}</td>
                  <td>
                    <span className={`role-badge ${currentUser.role}`}>{currentUser.role}</span>
                  </td>
                  <td>
                    <span className={currentUser.is_active ? 'active' : 'inactive'}>
                      {currentUser.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td>
                    <button className="action-btn">Edit</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
