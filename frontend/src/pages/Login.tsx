import { useState } from 'react';

interface LoginProps {
  onLogin: (email: string, password: string) => Promise<void>;
  onGitHubLogin: () => Promise<void>;
}

export default function Login({ onLogin, onGitHubLogin }: LoginProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await onLogin(email, password);
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  const handleGitHubLogin = async (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await onGitHubLogin();
    } catch (err: any) {
      setError(err.message || 'GitHub login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-container">
        <div className="login-header">
          <h1>SRE Agent</h1>
          <p>Self-Healing CI/CD Platform</p>
        </div>

        <form onSubmit={handleSubmit} className="login-form">
          <div className="form-group">
            <label htmlFor="email">Email</label>
            <input
              type="email"
              id="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input
              type="password"
              id="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="********"
              required
            />
          </div>

          {error && <div className="error-message">{error}</div>}

          <button type="submit" disabled={loading} className="login-button">
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        <div className="login-footer">
          <a href="#" className="oauth-link" onClick={handleGitHubLogin}>
            <span>GitHub</span> Sign in with GitHub
          </a>
          <a href="#" className="oauth-link">
            <span>Google</span> Sign in with Google
          </a>
        </div>
      </div>
    </div>
  );
}
