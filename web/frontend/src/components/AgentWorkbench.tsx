import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  createConversation,
  fetchAgents,
  fetchExperts,
  fetchSkills,
  fetchTools,
  updateAgent,
  type Agent,
  type Expert,
  type SkillListItem,
  type ToolInfo,
} from '../api/client';

interface FormState {
  name: string;
  systemPrompt: string;
  enabledSkills: string[];
  enabledTools: string[];
  temperature: string;
}

const EMPTY_FORM: FormState = {
  name: '',
  systemPrompt: '',
  enabledSkills: [],
  enabledTools: [],
  temperature: '',
};

export default function AgentWorkbench() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [experts, setExperts] = useState<Expert[]>([]);
  const [skills, setSkills] = useState<SkillListItem[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [savedNote, setSavedNote] = useState('');

  useEffect(() => { load(); }, []);

  useEffect(() => {
    const agent = agents.find(a => a.id === selectedId);
    if (agent) setForm(formFromAgent(agent, skills, tools));
  }, [selectedId, agents, skills, tools]);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [agentData, expertData, skillData, toolData] = await Promise.all([
        fetchAgents(),
        fetchExperts(),
        fetchSkills(),
        fetchTools(),
      ]);
      setAgents(agentData);
      setExperts(expertData);
      setSkills(skillData);
      setTools(toolData);
      const first = agentData.find(a => a.id !== 'default-agent') || agentData[0];
      if (first) {
        setSelectedId(first.id);
        setForm(formFromAgent(first, skillData, toolData));
      }
    } catch (e: any) {
      setError(e.message || '加载 Agent 配置失败');
    }
    setLoading(false);
  }

  const selectedAgent = useMemo(
    () => agents.find(a => a.id === selectedId) || null,
    [agents, selectedId],
  );

  const sourceExpert = useMemo(
    () => selectedAgent ? experts.find(e => e.name === selectedAgent.source_expert) || null : null,
    [experts, selectedAgent],
  );

  function toggleSkill(name: string) {
    setForm(f => ({
      ...f,
      enabledSkills: f.enabledSkills.includes(name)
        ? f.enabledSkills.filter(s => s !== name)
        : [...f.enabledSkills, name],
    }));
  }

  function toggleTool(name: string) {
    setForm(f => ({
      ...f,
      enabledTools: f.enabledTools.includes(name)
        ? f.enabledTools.filter(t => t !== name)
        : [...f.enabledTools, name],
    }));
  }

  function restoreFromExpert() {
    if (!sourceExpert) return;
    const model = sourceExpert.default_model || {};
    const skillNames = new Set(skills.map(s => s.name));
    const toolNames = new Set(tools.map(t => t.name));
    setForm(f => ({
      ...f,
      systemPrompt: sourceExpert.system_prompt,
      enabledSkills: (sourceExpert.default_skills || []).filter(name => skillNames.has(name)),
      enabledTools: (sourceExpert.default_tools || []).filter(name => toolNames.has(name)),
      temperature: model.temperature == null ? '' : String(model.temperature),
    }));
  }

  async function handleSave() {
    if (!selectedAgent) return;
    setSaving(true);
    setError('');
    setSavedNote('');
    try {
      const modelConfig: Record<string, unknown> = {};
      if (form.temperature.trim()) {
        modelConfig.temperature = Number(form.temperature);
      }
      const request = {
        name: form.name.trim() || selectedAgent.name,
        system_prompt: form.systemPrompt,
        enabled_skills: form.enabledSkills,
        enabled_tools: form.enabledTools,
        ...(Object.keys(modelConfig).length > 0 ? { model_config: modelConfig } : {}),
      };
      const updated = await updateAgent(selectedAgent.id, request);
      setAgents(prev => prev.map(a => a.id === updated.id ? updated : a));
      setSavedNote('已保存');
    } catch (e: any) {
      setError(e.message || '保存失败');
    }
    setSaving(false);
  }

  async function startConversation() {
    if (!selectedAgent) return;
    const conv = await createConversation(selectedAgent.id);
    navigate(`/conversations/${conv.session_id}`);
  }

  if (loading) return <div className="p-6 text-gray-500">加载中...</div>;

  return (
    <div className="flex-1 min-h-0 flex bg-gray-50">
      <aside className="w-80 border-r bg-white overflow-y-auto">
        <div className="px-5 py-4 border-b">
          <h2 className="text-xl font-bold text-gray-900">Agent 工作台</h2>
          <p className="text-sm text-gray-500 mt-1">配置角色、技能和工具</p>
        </div>
        <div className="p-3 space-y-2">
          {agents.map(agent => (
            <button
              key={agent.id}
              onClick={() => setSelectedId(agent.id)}
              className={`w-full text-left rounded-lg border px-4 py-3 transition-colors ${
                selectedId === agent.id
                  ? 'border-blue-400 bg-blue-50'
                  : 'border-gray-200 bg-white hover:bg-gray-50'
              }`}
            >
              <div className="font-medium text-gray-900 truncate">{agent.name}</div>
              <div className="text-xs text-gray-500 mt-1 truncate">
                {agent.source_expert || 'default'} · {agent.enabled_skills.length} 技能 · {agent.enabled_tools.length} 工具
              </div>
            </button>
          ))}
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        {!selectedAgent ? (
          <div className="p-8 text-gray-500">暂无 Agent</div>
        ) : (
          <div className="max-w-5xl mx-auto p-6 space-y-5">
            <div className="flex items-center justify-between gap-4">
              <div>
                <h3 className="text-2xl font-bold text-gray-900">{form.name || selectedAgent.name}</h3>
                <p className="text-sm text-gray-500 mt-1">
                  来源专家：{sourceExpert?.display_name || selectedAgent.source_expert || '默认'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {savedNote && <span className="text-sm text-green-600">{savedNote}</span>}
                <button
                  onClick={startConversation}
                  className="px-4 py-2 text-sm rounded-lg border border-blue-300 text-blue-700 hover:bg-blue-50"
                >
                  创建对话
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {saving ? '保存中...' : '保存配置'}
                </button>
              </div>
            </div>

            {error && <div className="px-4 py-2 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>}

            <section className="bg-white border rounded-lg p-5">
              <div className="flex items-center justify-between mb-4">
                <h4 className="font-semibold text-gray-900">基础配置</h4>
                <button
                  onClick={restoreFromExpert}
                  disabled={!sourceExpert}
                  className="text-sm text-gray-500 hover:text-blue-600 disabled:opacity-40"
                >
                  恢复专家默认
                </button>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <label className="block">
                  <span className="text-sm text-gray-600">名称</span>
                  <input
                    value={form.name}
                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                    className="mt-1 w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                  />
                </label>
                <div className="rounded-lg border bg-gray-50 px-3 py-2 text-sm text-gray-500">
                  <div className="text-gray-600">Model</div>
                  <div className="mt-1 font-mono text-gray-700">fixed by claw runtime</div>
                </div>
              </div>
              <label className="block mt-4 max-w-xs">
                <span className="text-sm text-gray-600">Temperature</span>
                <input
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  value={form.temperature}
                  onChange={e => setForm(f => ({ ...f, temperature: e.target.value }))}
                  className="mt-1 w-full px-3 py-2 border rounded-lg text-sm"
                />
              </label>
              <label className="block mt-4">
                <span className="text-sm text-gray-600">System Prompt</span>
                <textarea
                  value={form.systemPrompt}
                  onChange={e => setForm(f => ({ ...f, systemPrompt: e.target.value }))}
                  rows={9}
                  className="mt-1 w-full px-3 py-2 border rounded-lg text-sm leading-6 font-mono focus:outline-none focus:ring-2 focus:ring-blue-400"
                />
              </label>
            </section>

            <section className="grid grid-cols-1 xl:grid-cols-2 gap-5">
              <CapabilityPanel
                title="技能"
                emptyText="暂无技能"
                items={skills.map(s => ({ name: s.name, description: s.description, meta: s.category || s.version }))}
                selected={form.enabledSkills}
                onToggle={toggleSkill}
              />
              <CapabilityPanel
                title="工具"
                emptyText="暂无工具"
                items={tools.map(t => ({ name: t.name, description: t.description }))}
                selected={form.enabledTools}
                onToggle={toggleTool}
              />
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

function formFromAgent(agent: Agent, skills: SkillListItem[] = [], tools: ToolInfo[] = []): FormState {
  const model = agent.model_config || agent.llm_model || {};
  const skillNames = new Set(skills.map(s => s.name));
  const toolNames = new Set(tools.map(t => t.name));
  const enabledSkills = skills.length
    ? agent.enabled_skills.filter(name => skillNames.has(name))
    : [...agent.enabled_skills];
  const enabledTools = tools.length
    ? agent.enabled_tools.filter(name => toolNames.has(name))
    : [...agent.enabled_tools];
  return {
    name: agent.name,
    systemPrompt: agent.system_prompt,
    enabledSkills,
    enabledTools,
    temperature: model.temperature == null ? '' : String(model.temperature),
  };
}

function CapabilityPanel({
  title,
  emptyText,
  items,
  selected,
  onToggle,
}: {
  title: string;
  emptyText: string;
  items: { name: string; description: string; meta?: string }[];
  selected: string[];
  onToggle: (name: string) => void;
}) {
  return (
    <div className="bg-white border rounded-lg p-5">
      <div className="flex items-center justify-between mb-3">
        <h4 className="font-semibold text-gray-900">{title}</h4>
        <span className="text-xs text-gray-500">{selected.length} 已启用</span>
      </div>
      {items.length === 0 ? (
        <div className="py-8 text-center text-sm text-gray-400">{emptyText}</div>
      ) : (
        <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
          {items.map(item => {
            const checked = selected.includes(item.name);
            return (
              <label
                key={item.name}
                className={`flex gap-3 rounded-lg border px-3 py-3 cursor-pointer transition-colors ${
                  checked ? 'border-blue-300 bg-blue-50' : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggle(item.name)}
                  className="mt-1 h-4 w-4"
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <span className="font-medium text-sm text-gray-900 truncate">{item.name}</span>
                    {item.meta && <span className="text-xs text-gray-400 shrink-0">{item.meta}</span>}
                  </span>
                  <span className="block text-xs text-gray-500 mt-1 leading-5">{item.description}</span>
                </span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
