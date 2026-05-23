const API_BASE = '/api/v1';

// GET 请求去重：相同 URL 在 100ms 内复用同一个 Promise，避免 StrictMode 双调用
const _pending = new Map<string, Promise<unknown>>();

async function dedupedFetch<T>(url: string): Promise<T> {
  const existing = _pending.get(url);
  if (existing) return existing as Promise<T>;

  const p = fetch(url).then(r => r.json()).finally(() => _pending.delete(url));
  _pending.set(url, p);
  return p;
}

export interface Expert {
  name: string;
  display_name: string;
  description: string;
  system_prompt: string;
  default_tools: string[];
  default_skills: string[];
  default_mcp_servers?: string[];
  default_model?: Record<string, unknown>;
  default_memory?: Record<string, unknown>;
  default_sandbox?: Record<string, unknown>;
  meta: { avatar: string; tags: string[]; category: string };
  source: string;
}

export interface Agent {
  id: string;
  name: string;
  source_expert: string;
  system_prompt: string;
  enabled_tools: string[];
  enabled_skills: string[];
  enabled_mcp_servers?: string[];
  llm_model?: Record<string, unknown>;
  model_config?: Record<string, unknown>;
  memory_config?: Record<string, unknown>;
  sandbox_config?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface UpdateAgentRequest {
  name?: string;
  system_prompt?: string;
  enabled_skills?: string[];
  enabled_tools?: string[];
  enabled_mcp_servers?: string[];
  model_config?: Record<string, unknown>;
}

export interface ToolInfo {
  name: string;
  description: string;
  parameters: Record<string, unknown> | null;
}

export interface Conversation {
  session_id: string;
  agent_id: string;
  summary: string | null;
  messages: Message[];
}

export interface ConversationListItem {
  session_id: string;
  agent_id: string;
  summary: string | null;
  message_count: number;
  session_type?: string | null;
}

export interface Message {
  role: string;
  content: string;
  ts?: number | null;
}

export async function fetchExperts(q?: string): Promise<Expert[]> {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  return dedupedFetch<Expert[]>(`${API_BASE}/experts?${params}`);
}

export async function installExpert(name: string): Promise<Expert> {
  const resp = await fetch(`${API_BASE}/experts/${name}/install`, { method: 'POST' });
  return resp.json();
}

export async function uninstallExpert(name: string): Promise<void> {
  await fetch(`${API_BASE}/experts/${name}`, { method: 'DELETE' });
}

export async function deleteAgent(agentId: string): Promise<void> {
  await fetch(`${API_BASE}/agents/${agentId}`, { method: 'DELETE' });
}

export async function fetchAgents(): Promise<Agent[]> {
  return dedupedFetch<Agent[]>(`${API_BASE}/agents`);
}

export async function fetchAgent(agentId: string): Promise<Agent> {
  return dedupedFetch<Agent>(`${API_BASE}/agents/${encodeURIComponent(agentId)}`);
}

export async function createAgent(expertName: string, agentName?: string): Promise<Agent> {
  const resp = await fetch(`${API_BASE}/agents`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expert_name: expertName, agent_name: agentName }),
  });
  return resp.json();
}

export async function updateAgent(agentId: string, req: UpdateAgentRequest): Promise<Agent> {
  const resp = await fetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.detail || 'Update failed');
  }
  return resp.json();
}

export async function fetchTools(): Promise<ToolInfo[]> {
  return dedupedFetch<ToolInfo[]>(`${API_BASE}/tools`);
}

export async function createConversation(agentId: string): Promise<Conversation> {
  const resp = await fetch(`${API_BASE}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_id: agentId }),
  });
  return resp.json();
}

export async function fetchConversations(type?: string): Promise<ConversationListItem[]> {
  const params = type ? `?type=${type}` : '';
  return dedupedFetch<ConversationListItem[]>(`${API_BASE}/conversations${params}`);
}

export async function deleteConversation(sessionId: string): Promise<void> {
  await fetch(`${API_BASE}/conversations/${sessionId}`, { method: 'DELETE' });
}

export async function fetchConversation(sessionId: string): Promise<Conversation> {
  const resp = await fetch(`${API_BASE}/conversations/${sessionId}`);
  return resp.json();
}

export async function* streamChat(sessionId: string, text: string) {
  const resp = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text }),
  });
  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data: ')) continue;
      const data = trimmed.slice(6);
      if (data === '[DONE]') return;
      try {
        const parsed = JSON.parse(data);
        yield parsed;
      } catch { /* skip malformed */ }
    }
  }
}

// ---- Scheduled Tasks ----

export interface TriggerConfig {
  type: 'cron' | 'interval';
  expression?: string;
  seconds?: number;
}

export interface ScheduledTask {
  name: string;
  description: string;
  trigger: TriggerConfig;
  task_type: 'llm' | 'system';
  enabled: boolean;
  peer_key: string | null;
  prompt: string | null;
  agent_id: string | null;
  session_id: string | null;
  is_running: boolean;
  last_success: boolean | null;
  last_message: string;
  last_error: string | null;
}

export interface TaskDetail extends ScheduledTask {
  history: TaskRunRecord[];
}

export interface TaskRunRecord {
  task_name: string;
  triggered_at: string;
  completed_at: string;
  success: boolean;
  task_type: string;
  message: string;
  error: string | null;
}

export interface CreateTaskRequest {
  name: string;
  description?: string;
  trigger: TriggerConfig;
  agent_id: string;
  prompt: string;
  enabled?: boolean;
}

export interface UpdateTaskRequest {
  description?: string;
  trigger?: TriggerConfig;
  prompt?: string;
  enabled?: boolean;
}

export interface TriggerResult {
  success: boolean;
  message: string;
  error: string | null;
}

export async function fetchTasks(): Promise<ScheduledTask[]> {
  return dedupedFetch<ScheduledTask[]>(`${API_BASE}/tasks`);
}

export async function fetchTask(name: string): Promise<TaskDetail> {
  const resp = await fetch(`${API_BASE}/tasks/${encodeURIComponent(name)}`);
  return resp.json();
}

export async function createTask(req: CreateTaskRequest): Promise<ScheduledTask> {
  const resp = await fetch(`${API_BASE}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  return resp.json();
}

export async function updateTask(name: string, req: UpdateTaskRequest): Promise<ScheduledTask> {
  const resp = await fetch(`${API_BASE}/tasks/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  return resp.json();
}

export async function toggleTask(name: string, enabled: boolean): Promise<ScheduledTask> {
  const resp = await fetch(`${API_BASE}/tasks/${encodeURIComponent(name)}/toggle`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  return resp.json();
}

export async function triggerTask(name: string): Promise<TriggerResult> {
  const resp = await fetch(`${API_BASE}/tasks/${encodeURIComponent(name)}/trigger`, {
    method: 'POST',
  });
  return resp.json();
}

export async function fetchTaskHistory(name: string, limit = 20): Promise<TaskRunRecord[]> {
  return dedupedFetch<TaskRunRecord[]>(
    `${API_BASE}/tasks/${encodeURIComponent(name)}/history?limit=${limit}`
  );
}

export async function deleteTask(name: string): Promise<void> {
  await fetch(`${API_BASE}/tasks/${encodeURIComponent(name)}`, { method: 'DELETE' });
}

// ---- Skills ----

export interface SkillMeta {
  version: string;
  author: string;
  tags: string[];
  category: string;
}

export interface SkillListItem {
  name: string;
  description: string;
  source: string;
  version: string;
  tools: string[];
  category: string;
}

export interface Skill extends SkillListItem {
  instructions: string;
  meta: SkillMeta;
  path: string | null;
}

export async function fetchSkills(q?: string): Promise<SkillListItem[]> {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  return dedupedFetch<SkillListItem[]>(`${API_BASE}/skills?${params}`);
}

export async function fetchSkill(name: string): Promise<Skill> {
  const resp = await fetch(`${API_BASE}/skills/${encodeURIComponent(name)}`);
  return resp.json();
}

export async function installSkillFromFile(file: File): Promise<Skill> {
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`${API_BASE}/skills/install/file`, { method: 'POST', body: form });
  if (!resp.ok) throw new Error((await resp.json()).detail || 'Install failed');
  return resp.json();
}

export async function installSkillFromZip(file: File): Promise<Skill[]> {
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`${API_BASE}/skills/install/zip`, { method: 'POST', body: form });
  if (!resp.ok) throw new Error((await resp.json()).detail || 'Install failed');
  return resp.json();
}

export async function uninstallSkill(name: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/skills/${encodeURIComponent(name)}`, { method: 'DELETE' });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.detail || 'Uninstall failed');
  }
}

export async function exportSkill(name: string): Promise<Blob> {
  const resp = await fetch(`${API_BASE}/skills/${encodeURIComponent(name)}/export`);
  return resp.blob();
}

export async function exportSkills(names: string[]): Promise<Blob> {
  const resp = await fetch(`${API_BASE}/skills/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names }),
  });
  return resp.blob();
}
