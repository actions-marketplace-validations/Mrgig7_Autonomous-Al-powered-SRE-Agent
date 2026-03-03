import { useState } from 'react';

import type { User } from '../App';

interface LandingProps {
  user: User | null;
  onGitHubLogin: () => Promise<void>;
}

export default function Landing({ user, onGitHubLogin }: LandingProps) {
  const [error, setError] = useState('');

  const handleGitHubLogin = async () => {
    setError('');
    try {
      await onGitHubLogin();
    } catch (err: any) {
      setError(err?.message || 'GitHub login could not be started.');
    }
  };

  return (
    <div className="landing-page">
      <header className="landing-nav">
        <div className="landing-logo">SRE Agent</div>
        <div className="landing-nav-actions">
          {user ? (
            <a className="cta-secondary" href="/app">
              Open Dashboard
            </a>
          ) : (
            <button className="cta-secondary" onClick={handleGitHubLogin}>
              Login with GitHub
            </button>
          )}
        </div>
      </header>

      <section className="hero">
        <p className="hero-kicker">Autonomous AI-powered SRE Platform</p>
        <h1>Detect failures, understand root cause, and ship safer fixes.</h1>
        <p className="hero-copy">
          Start with visibility and controlled onboarding. Connect GitHub, select repositories,
          and choose the automation mode that fits your team's risk profile.
        </p>
        <div className="hero-actions">
          <button className="cta-primary" onClick={handleGitHubLogin}>
            Login with GitHub
          </button>
          <a className="cta-secondary" href="/app">
            View Dashboard
          </a>
        </div>
        {error && <div className="error-message">{error}</div>}
      </section>

      <section className="feature-grid">
        <article className="feature-card">
          <h3>Incident Intelligence</h3>
          <p>
            Correlate CI failures, historical incidents, and validation outcomes in one operating
            console.
          </p>
        </article>
        <article className="feature-card">
          <h3>Guardrailed Automation</h3>
          <p>
            Enforce policy checks, protected path rules, and sandbox validation before proposing
            any remediation.
          </p>
        </article>
        <article className="feature-card">
          <h3>Phase-wise Rollout</h3>
          <p>
            Begin with onboarding and observability controls now, then enable deeper autonomous
            workflows in later phases.
          </p>
        </article>
      </section>

      <section className="architecture-preview">
        <h2>Architecture Preview</h2>
        <div className="architecture-flow">
          <span>GitHub OAuth</span>
          <span>Repository Selection</span>
          <span>App Install</span>
          <span>Dashboard Control Plane</span>
        </div>
      </section>
    </div>
  );
}
