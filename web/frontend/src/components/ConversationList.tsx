import { useEffect, useState } from 'react';
import { fetchConversations, fetchAgents, createConversation, deleteConversation, type ConversationListItem, type Agent } from '../api/client';

interface Props {
  onSelect: (conv: ConversationListItem) => void;
  onConversationCreated: (sessionId: string) => void;
}

export default function ConversationList({ onSelect, onConversationCreated }: Props) {
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNewChat, setShowNewChat] = useState(false);

  useEffect(() => { load(); }, []);

  async function load() {
    setLoading(true);
    const [convs, ags] = await Promise.all([fetchConversations(), fetchAgents()]);
    setConversations(convs);
    setAgents(ags);
    setLoading(false);
  }

  async function handleNewChat(agentId: string) {
    const conv = await createConversation(agentId);
    setShowNewChat(false);
    onConversationCreated(conv.session_id);
  }

  async function handleDelete(sessionId: string, e: React.MouseEvent) {
    e.stopPropagation();
    await deleteConversation(sessionId);
    await load();
  }

  function getAgentName(agentId: string) {
    return agents.find(a => a.id === agentId)?.name || agentId;
  }

  // 排除 default-agent，只展示已安装的专家 Agent
  const installedAgents = agents.filter(a => a.id !== 'default-agent');

  if (loading) return <div className="p-6 text-gray-500">加载中...</div>;

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-gray-800">对话列表</h2>
        <button
          onClick={() => setShowNewChat(!showNewChat)}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm"
        >
          新对话
        </button>
      </div>

      {showNewChat && (
        <div className="bg-white rounded-xl border p-4 mb-4">
          <h3 className="font-medium text-gray-700 mb-3">选择 Agent</h3>
          <div className="flex flex-wrap gap-2">
            {installedAgents.map(agent => (
              <button
                key={agent.id}
                onClick={() => handleNewChat(agent.id)}
                className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-blue-50 hover:text-blue-700 text-sm"
              >
                {agent.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {conversations.length === 0 ? (
        <div className="text-center py-12 text-gray-400">
          <p className="text-lg mb-2">暂无对话</p>
          <p className="text-sm">点击"新对话"或从专家广场创建</p>
        </div>
      ) : (
        <div className="space-y-2">
          {conversations.map(conv => (
            <div
              key={conv.session_id}
              onClick={() => onSelect(conv)}
              className="bg-white rounded-lg border p-4 hover:bg-blue-50 cursor-pointer transition-colors flex items-center justify-between"
            >
              <div className="min-w-0">
                <p className="font-medium text-gray-800 truncate">
                  {conv.summary || `对话 ${conv.session_id.slice(0, 12)}...`}
                </p>
                <p className="text-sm text-gray-500">
                  {getAgentName(conv.agent_id)} · {conv.message_count} 条消息
                </p>
              </div>
              <button
                onClick={(e) => handleDelete(conv.session_id, e)}
                className="text-gray-300 hover:text-red-500 ml-2"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
