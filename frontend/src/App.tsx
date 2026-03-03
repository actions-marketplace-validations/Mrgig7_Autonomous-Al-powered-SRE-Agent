import { BrowserRouter as Router, Navigate, Route, Routes } from 'react-router-dom';
import { useEffect, useState } from 'react';

import api from './api/client';
import Dashboard from './pages/Dashboard';
import Landing from './pages/Landing';
import Login from './pages/Login';
import OAuthCallback from './pages/OAuthCallback';

export interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  permissions: string[];
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const checkAuth = async () => {
      if (!api.hasSessionHint()) {
        setUser(null);
        setLoading(false);
        return;
      }

      try {
        const profile = await api.getProfile();
        setUser(profile);
      } catch {
        setUser(null);
      } finally {
        setLoading(false);
      }
    };

    const handleSessionExpired = () => {
      setUser(null);
    };

    window.addEventListener('sre-session-expired', handleSessionExpired);
    checkAuth();

    return () => {
      window.removeEventListener('sre-session-expired', handleSessionExpired);
    };
  }, []);

  const handleLogin = async (email: string, password: string) => {
    await api.login(email, password);
    const profile = await api.getProfile();
    setUser(profile);
  };

  const handleGitHubLoginStart = async () => {
    const { authorization_url } = await api.startGitHubLogin();
    window.location.href = authorization_url;
  };

  const handleGitHubLoginExchange = async (code: string, state: string) => {
    await api.exchangeGitHubCode(code, state);
    const profile = await api.getProfile();
    setUser(profile);
  };

  const handleLogout = async () => {
    await api.logout();
    setUser(null);
  };

  if (loading) {
    return (
      <div className="loading-screen">
        <div className="loading-spinner"></div>
        <p>Loading SRE Agent...</p>
      </div>
    );
  }

  return (
    <Router>
      <Routes>
        <Route
          path="/"
          element={<Landing user={user} onGitHubLogin={handleGitHubLoginStart} />}
        />
        <Route
          path="/oauth/github/callback"
          element={<OAuthCallback onExchange={handleGitHubLoginExchange} />}
        />
        <Route
          path="/login"
          element={
            user ? (
              <Navigate to="/app" replace />
            ) : (
              <Login onLogin={handleLogin} onGitHubLogin={handleGitHubLoginStart} />
            )
          }
        />
        <Route
          path="/app/*"
          element={
            user ? <Dashboard user={user} onLogout={handleLogout} /> : <Navigate to="/" replace />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Router>
  );
}

export default App;
