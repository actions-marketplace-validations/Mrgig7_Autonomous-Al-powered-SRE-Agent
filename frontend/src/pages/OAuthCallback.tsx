import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import api from '../api/client';

interface OAuthCallbackProps {
  onExchange: (code: string, state: string) => Promise<void>;
}

export default function OAuthCallback({ onExchange }: OAuthCallbackProps) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const exchangeStarted = useRef(false);

  const handleRetry = async () => {
    try {
      const { authorization_url } = await api.startGitHubLogin();
      window.location.href = authorization_url;
    } catch {
      navigate('/', { replace: true });
    }
  };

  useEffect(() => {
    const run = async () => {
      const oauthError = searchParams.get('error');
      if (oauthError) {
        setError(`GitHub OAuth failed: ${oauthError}`);
        return;
      }

      const installationIdRaw = searchParams.get('installation_id');
      const setupAction = searchParams.get('setup_action') || undefined;
      const state = searchParams.get('state');
      if (installationIdRaw && state) {
        const installationId = Number(installationIdRaw);
        if (!Number.isFinite(installationId) || installationId <= 0) {
          setError('Invalid installation callback payload.');
          return;
        }

        try {
          const result = await api.confirmIntegrationInstall(state, installationId, setupAction);
          const query = new URLSearchParams();
          query.set('install', result.status);
          if (result.repository) {
            query.set('repo', result.repository);
          }
          navigate(`/app?${query.toString()}`, { replace: true });
        } catch (err: any) {
          setError(err?.message || 'Failed to confirm GitHub App installation.');
        }
        return;
      }

      const code = searchParams.get('code');
      if (!code || !state) {
        setError('Missing OAuth callback parameters.');
        return;
      }

      // Guard against React StrictMode double-mount calling exchange twice
      if (exchangeStarted.current) {
        return;
      }
      exchangeStarted.current = true;

      try {
        await onExchange(code, state);
        navigate('/app', { replace: true });
      } catch (err: any) {
        setError(err?.message || 'GitHub login failed.');
      }
    };

    run();
  }, [navigate, onExchange, searchParams]);

  return (
    <div className="oauth-callback-page">
      <div className="oauth-callback-card">
        {error ? (
          <>
            <h2>Authentication Failed</h2>
            <p>{error}</p>
            <div style={{ display: 'flex', gap: '12px', marginTop: '8px' }}>
              <button onClick={handleRetry} className="cta-primary" style={{ cursor: 'pointer' }}>
                Try Again
              </button>
              <a href="/" className="cta-secondary">
                Back to Landing
              </a>
            </div>
          </>
        ) : (
          <>
            <h2>Completing Sign-In</h2>
            <p>Finalizing your GitHub session and loading dashboard access.</p>
            <div className="loading-spinner"></div>
          </>
        )}
      </div>
    </div>
  );
}
