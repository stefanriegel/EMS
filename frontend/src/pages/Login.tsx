import React, { useState } from "react";
import { useLocation } from "wouter";

export function Login() {
  const [, setLocation] = useLocation();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [password, setPassword] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("./api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        setLocation("/");
      } else {
        setError("Incorrect password");
      }
    } catch {
      setError("Connection error — is the EMS running?");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app" style={{ justifyContent: "center", alignItems: "center", minHeight: "100vh", display: "flex" }}>
      <div className="card" style={{ maxWidth: 360, width: "100%", padding: "2rem" }}>
        <h1 className="app-title" style={{ fontSize: "1.4rem", marginBottom: "1.5rem" }}>
          EMS Login
        </h1>
        <form onSubmit={handleSubmit} data-testid="login-form">
          <label className="setup-label" htmlFor="ems-password">
            Admin Password
          </label>
          <input
            id="ems-password"
            type="password"
            className="setup-input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Enter password"
            data-testid="password-input"
            autoComplete="current-password"
            required
          />
          {error && (
            <p style={{ color: "var(--color-danger, #ef4444)", marginTop: "0.5rem", fontSize: "0.9rem" }}>
              {error}
            </p>
          )}
          <button
            type="submit"
            className="btn btn--primary"
            style={{ width: "100%", marginTop: "1.25rem" }}
            disabled={loading}
            data-testid="login-btn"
          >
            {loading ? "Logging in…" : "Login"}
          </button>
        </form>
        <p style={{ marginTop: "1.5rem", fontSize: "0.8rem", opacity: 0.5, textAlign: "center" }}>
          EMS · M004
        </p>
      </div>
    </div>
  );
}
