import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { getRuntimeConfig, updateRuntimeConfig } from '../api/client';
import type { LlmProvider, RunMode, RuntimeConfig, RuntimeConfigUpdatePayload } from '../types/report';

interface StartupFormState {
  defaultRunMode: RunMode;
  llmProvider: LlmProvider;
  llmBaseUrl: string;
  llmPrimaryModel: string;
  llmReviewerModel: string;
  llmShadowModel: string;
  llmShadowReviewerModel: string;
  llmApiKey: string;
}

function buildFormState(config: RuntimeConfig): StartupFormState {
  return {
    defaultRunMode: config.default_run_mode,
    llmProvider: config.llm_provider,
    llmBaseUrl: config.llm_base_url,
    llmPrimaryModel: config.llm_primary_model,
    llmReviewerModel: config.llm_reviewer_model,
    llmShadowModel: config.llm_shadow_model,
    llmShadowReviewerModel: config.llm_shadow_reviewer_model,
    llmApiKey: ''
  };
}

export function StartupPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [apiKeyTouched, setApiKeyTouched] = useState(false);
  const [apiKeyStatus, setApiKeyStatus] = useState('Loading API key status...');
  const [error, setError] = useState('');
  const [form, setForm] = useState<StartupFormState>({
    defaultRunMode: 'LIVE',
    llmProvider: 'mock',
    llmBaseUrl: 'https://api.openai.com/v1',
    llmPrimaryModel: 'mock-primary-v1',
    llmReviewerModel: 'NONE',
    llmShadowModel: 'mock-challenger-v1',
    llmShadowReviewerModel: 'NONE',
    llmApiKey: ''
  });

  useEffect(() => {
    let cancelled = false;

    async function loadRuntimeConfig() {
      try {
        const config = await getRuntimeConfig();
        if (cancelled) {
          return;
        }
        setForm(buildFormState(config));
        setApiKeyStatus(
          config.llm_api_key_set
            ? `API Key is configured (${config.llm_api_key_masked || 'hidden'}).`
            : 'API Key is not configured.'
        );
        setError('');
      } catch (loadError) {
        if (!cancelled) {
          const message = loadError instanceof Error ? loadError.message : 'Unknown load error';
          setError(message);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadRuntimeConfig();
    return () => {
      cancelled = true;
    };
  }, []);

  const canSubmit = useMemo(() => {
    return Boolean(
      form.llmBaseUrl.trim() &&
      form.llmPrimaryModel.trim() &&
      form.llmReviewerModel.trim() &&
      form.llmShadowModel.trim() &&
      form.llmShadowReviewerModel.trim()
    );
  }, [form]);

  async function persist(redirectToGenerate: boolean) {
    if (!canSubmit || saving) {
      return;
    }

    setSaving(true);
    setError('');
    try {
      const payload: RuntimeConfigUpdatePayload = {
        default_run_mode: form.defaultRunMode,
        llm_provider: form.llmProvider,
        llm_base_url: form.llmBaseUrl.trim(),
        llm_primary_model: form.llmPrimaryModel.trim(),
        llm_reviewer_model: form.llmReviewerModel.trim(),
        llm_shadow_model: form.llmShadowModel.trim(),
        llm_shadow_reviewer_model: form.llmShadowReviewerModel.trim()
      };

      if (apiKeyTouched) {
        payload.llm_api_key = form.llmApiKey.trim();
      }

      const saved = await updateRuntimeConfig(payload);
      setForm(buildFormState(saved));
      setApiKeyStatus(
        saved.llm_api_key_set
          ? `API Key is configured (${saved.llm_api_key_masked || 'hidden'}).`
          : 'API Key is not configured.'
      );
      setApiKeyTouched(false);
      if (redirectToGenerate) {
        navigate('/generate', { replace: true });
      }
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : 'Unknown save error';
      setError(message);
    } finally {
      setSaving(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await persist(false);
  }

  return (
    <main className="page-root">
      <div className="mesh" aria-hidden="true" />
      <section className="panel panel-intro">
        <p className="eyebrow">FiPro_1 GUI</p>
        <h1>Startup Configuration</h1>
        <p>Select default run mode and configure LLM provider/API settings before generating reports.</p>
      </section>

      <section className="panel panel-form">
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            <span>Default Run Mode</span>
            <select
              value={form.defaultRunMode}
              onChange={(event) => setForm((prev) => ({ ...prev, defaultRunMode: event.target.value as RunMode }))}
            >
              <option value="LIVE">LIVE</option>
              <option value="SHADOW">SHADOW</option>
              <option value="BACKTEST">BACKTEST</option>
            </select>
          </label>

          <label>
            <span>LLM Provider</span>
            <select
              value={form.llmProvider}
              onChange={(event) => setForm((prev) => ({ ...prev, llmProvider: event.target.value as LlmProvider }))}
            >
              <option value="mock">mock</option>
              <option value="openai">openai</option>
              <option value="openai_compatible">openai_compatible</option>
            </select>
          </label>

          <label className="wide">
            <span>LLM Base URL</span>
            <input
              value={form.llmBaseUrl}
              onChange={(event) => setForm((prev) => ({ ...prev, llmBaseUrl: event.target.value }))}
              placeholder="https://api.openai.com/v1"
              required
            />
          </label>

          <label>
            <span>Primary Model</span>
            <input
              value={form.llmPrimaryModel}
              onChange={(event) => setForm((prev) => ({ ...prev, llmPrimaryModel: event.target.value }))}
              placeholder="gpt-4o-mini"
              required
            />
          </label>

          <label>
            <span>Reviewer Model</span>
            <input
              value={form.llmReviewerModel}
              onChange={(event) => setForm((prev) => ({ ...prev, llmReviewerModel: event.target.value }))}
              placeholder="NONE"
              required
            />
          </label>

          <label>
            <span>Shadow Model</span>
            <input
              value={form.llmShadowModel}
              onChange={(event) => setForm((prev) => ({ ...prev, llmShadowModel: event.target.value }))}
              placeholder="gpt-4o-mini"
              required
            />
          </label>

          <label>
            <span>Shadow Reviewer Model</span>
            <input
              value={form.llmShadowReviewerModel}
              onChange={(event) => setForm((prev) => ({ ...prev, llmShadowReviewerModel: event.target.value }))}
              placeholder="NONE"
              required
            />
          </label>

          <label className="wide">
            <span>LLM API Key</span>
            <input
              type="password"
              value={form.llmApiKey}
              onChange={(event) => {
                setApiKeyTouched(true);
                setForm((prev) => ({ ...prev, llmApiKey: event.target.value }));
              }}
              placeholder={apiKeyTouched ? '' : 'Leave blank to keep current key'}
              autoComplete="off"
            />
            <p className="helper-text">{apiKeyStatus}</p>
          </label>

          <div className="wide actions">
            <div className="actions-inline">
              <button type="submit" disabled={loading || saving || !canSubmit}>
                {saving ? 'Saving...' : 'Save Configuration'}
              </button>
              <button
                type="button"
                disabled={loading || saving || !canSubmit}
                onClick={() => {
                  void persist(true);
                }}
              >
                Save and Go to Generate
              </button>
              <Link className="ghost-link" to="/generate">
                Skip to Generate
              </Link>
            </div>
            {error ? <p className="error-text">{error}</p> : null}
          </div>
        </form>
      </section>
    </main>
  );
}
