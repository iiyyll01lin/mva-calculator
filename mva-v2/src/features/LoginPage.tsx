import { useState } from 'react';

interface LoginPageProps {
  onLogin: () => void;
}

export function LoginPage({ onLogin }: LoginPageProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (username === 'Avery' && password === 'avery') {
      sessionStorage.setItem('mva_auth', '1');
      onLogin();
    } else {
      setError('Invalid credentials. Please try again.');
    }
  };

  return (
    <div className="login-shell">
      <div className="login-card">
        <div className="brand-block">
          <p className="eyebrow">MVA v2</p>
          <h1 className="brand-title">StreamWeaver</h1>
          <p className="author-line">Author: Jason YY Lin</p>
          <p className="muted" style={{ marginTop: '0.5rem' }}>
            Enterprise workspace access is restricted. Please sign in to continue.
          </p>
        </div>
        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            <span>Username</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              // eslint-disable-next-line jsx-a11y/no-autofocus
              autoFocus
              aria-label="Username"
            />
          </label>
          <label>
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              aria-label="Password"
            />
          </label>
          {error && (
            <p className="login-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="button primary login-submit">
            Sign In
          </button>
        </form>
      </div>
    </div>
  );
}
