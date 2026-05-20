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
  llm_model: Record<string, unknown>;
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
}

export interface Message {
  role: string;
  content: string;
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

export async function createAgent(expertName: string, agentName?: string): Promise<Agent> {
  const resp = await fetch(`${API_BASE}/agents`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expert_name: expertName, agent_name: agentName }),
  });
  return resp.json();
}

export async function createConversation(agentId: string): Promise<Conversation> {
  const resp = await fetch(`${API_BASE}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_id: agentId }),
  });
  return resp.json();
}

export async function fetchConversations(): Promise<ConversationListItem[]> {
  return dedupedFetch<ConversationListItem[]>(`${API_BASE}/conversations`);
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
