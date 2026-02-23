import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { getRuntimeConfig, updateRuntimeConfig } from '../api/client';
import { DataSourceStatusWidget } from '../components/DataSourceStatusWidget';
import type { RuntimeConfig, RuntimeConfigUpdatePayload, RuntimeLlmProfile } from '../types/report';

interface StartupFormState {
  llmProfileId: string;
  llmPrimaryModel: string;
  llmShadowModel: string;
}

function normalizeModels(values: Array<string | undefined>): string[] {
  const merged: string[] = [];
  for (const raw of values) {
    const value = String(raw || '').trim();
    if (!value || value.toUpperCase() === 'NONE') {
      continue;
    }
    if (!merged.includes(value)) {
      merged.push(value);
    }
    if (merged.length >= 3) {
      break;
    }
  }
  return merged;
}

function normalizeProfiles(config: RuntimeConfig): RuntimeLlmProfile[] {
  const source = Array.isArray(config.llm_profiles) ? config.llm_profiles : [];
  const profiles: RuntimeLlmProfile[] = [];
  for (const item of source) {
    const id = String(item?.id || '').trim();
    if (!id || profiles.some((existing) => existing.id === id)) {
      continue;
    }
    const available = normalizeModels([
      ...(Array.isArray(item.llm_available_models) ? item.llm_available_models : []),
      item.llm_primary_model,
      item.llm_shadow_model
    ]);
    profiles.push({
      id,
      label: String(item.label || id),
      llm_provider: item.llm_provider,
      llm_primary_model: String(item.llm_primary_model || ''),
      llm_shadow_model: String(item.llm_shadow_model || ''),
      llm_available_models: available,
      llm_api_key_set: Boolean(item.llm_api_key_set)
    });
  }
  if (profiles.length > 0) {
    return profiles;
  }
  const fallbackId = String(config.llm_profile_id || config.llm_provider || 'default').trim() || 'default';
  return [
    {
      id: fallbackId,
      label: fallbackId,
      llm_provider: config.llm_provider,
      llm_primary_model: config.llm_primary_model,
      llm_shadow_model: config.llm_shadow_model,
      llm_available_models: normalizeModels([
        ...(Array.isArray(config.llm_available_models) ? config.llm_available_models : []),
        config.llm_primary_model,
        config.llm_shadow_model
      ]),
      llm_api_key_set: config.llm_api_key_set
    }
  ];
}

function findProfile(profiles: RuntimeLlmProfile[], profileId: string): RuntimeLlmProfile | null {
  const normalized = profileId.trim();
  if (!normalized) {
    return profiles[0] || null;
  }
  return profiles.find((item) => item.id === normalized) || profiles[0] || null;
}

function profileModels(profile: RuntimeLlmProfile | null): string[] {
  if (!profile) {
    return [];
  }
  return normalizeModels([
    ...(Array.isArray(profile.llm_available_models) ? profile.llm_available_models : []),
    profile.llm_primary_model,
    profile.llm_shadow_model
  ]);
}

function buildFormState(config: RuntimeConfig, profiles: RuntimeLlmProfile[]): StartupFormState {
  const activeProfile = findProfile(profiles, config.llm_profile_id) || profiles[0] || null;
  const models = profileModels(activeProfile);
  const fallbackPrimary = models[0] || activeProfile?.llm_primary_model || '';
  const fallbackShadow = models[1] || models[0] || activeProfile?.llm_shadow_model || '';
  return {
    llmProfileId: activeProfile?.id || '',
    llmPrimaryModel: config.llm_primary_model && models.includes(config.llm_primary_model)
      ? config.llm_primary_model
      : fallbackPrimary,
    llmShadowModel: config.llm_shadow_model && models.includes(config.llm_shadow_model)
      ? config.llm_shadow_model
      : fallbackShadow,
  };
}

export function StartupPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [profiles, setProfiles] = useState<RuntimeLlmProfile[]>([]);
  const [form, setForm] = useState<StartupFormState>({
    llmProfileId: '',
    llmPrimaryModel: '',
    llmShadowModel: '',
  });

  useEffect(() => {
    let cancelled = false;

    async function loadRuntimeConfig() {
      try {
        const config = await getRuntimeConfig();
        if (cancelled) {
          return;
        }
        const allProfiles = normalizeProfiles(config);
        setProfiles(allProfiles);
        setForm(buildFormState(config, allProfiles));
        setError('');
      } catch (loadError) {
        if (!cancelled) {
          const message = loadError instanceof Error ? loadError.message : '加载失败（未知错误）';
          setError(message);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadRuntimeConfig();
    return () => {
      cancelled = true;
    };
  }, []);

  const canSubmit = useMemo(() => {
    const activeProfile = findProfile(profiles, form.llmProfileId);
    const models = profileModels(activeProfile);
    return Boolean(
      profiles.length > 0 &&
      form.llmProfileId.trim() &&
      models.length > 0 &&
      form.llmPrimaryModel.trim() &&
      form.llmShadowModel.trim()
    );
  }, [profiles, form.llmProfileId, form.llmPrimaryModel, form.llmShadowModel]);

  const activeProfile = useMemo(() => findProfile(profiles, form.llmProfileId), [profiles, form.llmProfileId]);
  const activeModels = useMemo(() => profileModels(activeProfile), [activeProfile]);
  const activeProfileNeedsKey = Boolean(activeProfile && activeProfile.llm_provider !== 'mock' && !activeProfile.llm_api_key_set);

  async function persist(redirectToGenerate: boolean) {
    if (!canSubmit || saving) {
      return;
    }

    setSaving(true);
    setError('');
    try {
      const payload: RuntimeConfigUpdatePayload = {
        llm_profile_id: form.llmProfileId.trim(),
        llm_primary_model: form.llmPrimaryModel.trim(),
        llm_shadow_model: form.llmShadowModel.trim(),
      };
      const saved = await updateRuntimeConfig(payload);
      const allProfiles = normalizeProfiles(saved);
      setProfiles(allProfiles);
      setForm(buildFormState(saved, allProfiles));
      if (redirectToGenerate) {
        navigate('/generate', { replace: true });
      }
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : '保存失败（未知错误）';
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
        <p className="eyebrow">FiPro_1 图形界面</p>
        <h1>启动配置</h1>
        <p>按项目预置方案选择 LLM 提供方与模型，连接参数自动随方案切换。</p>
        <div className="actions-row">
          <DataSourceStatusWidget />
        </div>
      </section>

      <section className="panel panel-form">
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            <span>LLM 提供方</span>
            <select
              value={form.llmProfileId}
              onChange={(event) => {
                const nextProfile = findProfile(profiles, event.target.value);
                const nextModels = profileModels(nextProfile);
                setForm((prev) => ({
                  ...prev,
                  llmProfileId: event.target.value,
                  llmPrimaryModel: nextModels[0] || nextProfile?.llm_primary_model || '',
                  llmShadowModel: nextModels[1] || nextModels[0] || nextProfile?.llm_shadow_model || '',
                }));
              }}
            >
              {profiles.length === 0 ? (
                <option value="">未发现预置方案</option>
              ) : (
                profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.label}
                  </option>
                ))
              )}
            </select>
          </label>

          <label>
            <span>主模型（LIVE）</span>
            <select
              value={form.llmPrimaryModel}
              onChange={(event) => setForm((prev) => ({ ...prev, llmPrimaryModel: event.target.value }))}
              disabled={activeModels.length === 0}
            >
              {activeModels.length === 0 ? (
                <option value="">未发现已配置模型</option>
              ) : (
                activeModels.map((model) => (
                  <option key={`primary-${model}`} value={model}>
                    {model}
                  </option>
                ))
              )}
            </select>
          </label>

          <label>
            <span>影子模型（SHADOW）</span>
            <select
              value={form.llmShadowModel}
              onChange={(event) => setForm((prev) => ({ ...prev, llmShadowModel: event.target.value }))}
              disabled={activeModels.length === 0}
            >
              {activeModels.length === 0 ? (
                <option value="">未发现已配置模型</option>
              ) : (
                activeModels.map((model) => (
                  <option key={`shadow-${model}`} value={model}>
                    {model}
                  </option>
                ))
              )}
            </select>
          </label>

          {!loading && profiles.length === 0 ? (
            <p className="wide error-text">未检测到预置 LLM 方案，请先在 .env 中配置。</p>
          ) : null}
          {activeProfileNeedsKey ? <p className="wide error-text">当前方案 API Key 未配置，调用将失败。</p> : null}
          <p className="wide helper-text">方案列表与模型列表均来自项目环境变量预置；连接参数不会在 GUI 中直接编辑。</p>

          <div className="wide actions">
            <div className="actions-inline">
              <button type="submit" disabled={loading || saving || !canSubmit}>
                {saving ? '保存中...' : '保存配置'}
              </button>
              <button
                type="button"
                disabled={loading || saving || !canSubmit}
                onClick={() => {
                  void persist(true);
                }}
              >
                保存并前往生成页
              </button>
              <Link className="ghost-link" to="/generate">
                跳过并前往生成页
              </Link>
              <Link className="ghost-link" to="/backtest">
                前往回测页
              </Link>
              <Link className="ghost-link" to="/proposals">
                提案评审
              </Link>
              <Link className="ghost-link" to="/champion-health">
                Champion 监控
              </Link>
            </div>
            {error ? <p className="error-text">{error}</p> : null}
          </div>
        </form>
      </section>
    </main>
  );
}
