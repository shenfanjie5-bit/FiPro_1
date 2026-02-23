import { ReactNode } from 'react';

interface AdvancedSettingsProps {
  summary?: string;
  description?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

export function AdvancedSettings({
  summary = '高级设置',
  description = '后续可在这里扩展更多高级参数。',
  defaultOpen = false,
  children
}: AdvancedSettingsProps) {
  return (
    <div className="wide advanced-block">
      <details className="advanced-settings" open={defaultOpen}>
        <summary>{summary}</summary>
        {description ? <p className="helper-text">{description}</p> : null}
        <div className="advanced-grid">{children}</div>
      </details>
    </div>
  );
}
