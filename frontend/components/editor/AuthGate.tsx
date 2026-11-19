"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import WorkflowEditor from "@/components/editor/WorkflowEditor";
import { api } from "@/lib/api";

interface TokenResponse {
  access_token: string;
  refresh_token: string;
}

const ACCESS_TOKEN_KEY = "agentos_access_token";
const REFRESH_TOKEN_KEY = "agentos_refresh_token";

export default function AuthGate() {
  const [authenticated, setAuthenticated] = useState(false);
  const [isSessionChecked, setIsSessionChecked] = useState(false);
  const [isRegistering, setIsRegistering] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setAuthenticated(Boolean(window.sessionStorage.getItem(ACCESS_TOKEN_KEY)));
    setIsSessionChecked(true);
  }, []);

  function signOut() {
    window.sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    window.sessionStorage.removeItem(REFRESH_TOKEN_KEY);
    setAuthenticated(false);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    try {
      const endpoint = isRegistering ? "/auth/register" : "/auth/login";
      const response = await api.post<TokenResponse>(endpoint, { email, password });

      window.sessionStorage.setItem(ACCESS_TOKEN_KEY, response.data.access_token);
      window.sessionStorage.setItem(REFRESH_TOKEN_KEY, response.data.refresh_token);
      setAuthenticated(true);
    } catch {
      setError("Sign in failed. Check your credentials and API connection.");
    }
  }

  if (!isSessionChecked) {
    return null;
  }

  if (authenticated) {
    return <WorkflowEditor onSignOut={signOut} />;
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-[#090d18] px-4 text-slate-100">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-xl border border-slate-800 bg-slate-900 p-7"
      >
        <div className="mb-6 flex items-center gap-2">
          <div className="h-8 w-8 rounded-lg bg-violet-600" />
          <strong>AgentOS</strong>
        </div>

        <h1 className="text-xl font-semibold">
          {isRegistering ? "Create your workspace" : "Welcome back"}
        </h1>
        <p className="mt-1 mb-6 text-sm text-slate-400">
          {isRegistering ? "Start building AI workflows." : "Sign in to your workspace."}
        </p>

        <label className="mb-4 block text-sm">
          Email
          <input
            required
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2"
          />
        </label>

        <label className="mb-4 block text-sm">
          Password
          <input
            required
            minLength={8}
            type="password"
            autoComplete={isRegistering ? "new-password" : "current-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2"
          />
        </label>

        {error && (
          <p role="alert" className="mb-3 text-sm text-red-400">
            {error}
          </p>
        )}

        <button className="w-full rounded-md bg-violet-600 px-3 py-2 text-sm font-medium">
          {isRegistering ? "Create account" : "Sign in"}
        </button>
        <button
          type="button"
          onClick={() => setIsRegistering((current) => !current)}
          className="mt-4 w-full text-sm text-slate-400 hover:text-white"
        >
          {isRegistering ? "Already have an account? Sign in" : "New to AgentOS? Create an account"}
        </button>
      </form>
    </main>
  );
}
