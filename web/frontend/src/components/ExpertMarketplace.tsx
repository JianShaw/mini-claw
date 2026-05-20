import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  fetchExperts, fetchAgents, fetchConversations,
  installExpert, uninstallExpert,
  createAgent, deleteAgent,
  createConversation, deleteConversation,
  type Expert, type Agent, type ConversationListItem,
} from '../api/client';

export default function ExpertMarketplace() {
  const navigate = useNavigate();
  const [experts, setExperts] = useState<Expert[]>([]);
  const [loading, setLoading] = useState(true);
  const [operating, setOperating] = useState<string | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    const [ex, ag] = await Promise.all([fetchExperts(), fetchAgents()]);
    setExperts(ex);
    setAgents(ag);
    setLoading(false);
  }

  async function handleInstall(name: string) {
    setOperating(name);
    await installExpert(name);
    // 复用已有 Agent，避免重复创建
    const existing = agents.find(a => a.source_expert === name);
    const agent = existing || await createAgent(name);
    const conv = await createConversation(agent.id);
    navigate(`/conversations/${conv.session_id}`);
  }

  async function handleUninstall(name: string) {
    setOperating(name);
    // 查找该专家关联的所有 agent
    const relatedAgents = agents.filter(a => a.source_expert === name);
    if (relatedAgents.length > 0) {
      // 查找并删除这些 agent 的所有对话
      const convs = await fetchConversations();
      const agentIds = new Set(relatedAgents.map(a => a.id));
      const relatedConvs = convs.filter(c => agentIds.has(c.agent_id));
      await Promise.all(relatedConvs.map(c => deleteConversation(c.session_id)));
      // 删除 agent
      await Promise.all(relatedAgents.map(a => deleteAgent(a.id)));
    }
    // 卸载专家
    await uninstallExpert(name);
    await load();
    setOperating(null);
  }

  async function handleCreateAgent(expertName: string) {
    // 复用已有 Agent，不再重复创建
    const existing = agents.find(a => a.source_expert === expertName);
    if (existing) {
      const conv = await createConversation(existing.id);
      navigate(`/conversations/${conv.session_id}`);
      return;
    }
    const agent = await createAgent(expertName);
    const conv = await createConversation(agent.id);
    navigate(`/conversations/${conv.session_id}`);
  }

  function isExpertInstalled(name: string) {
    return agents.some(a => a.source_expert === name);
  }

  if (loading) return <div className="p-6 text-gray-500">加载中...</div>;

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h2 className="text-2xl font-bold text-gray-800 mb-6">专家广场</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {experts.map(expert => (
          <div key={expert.name} className="bg-white rounded-xl border p-5 hover:shadow-md transition-shadow">
            <div className="flex items-start gap-3 mb-3">
              <span className="text-3xl">{expert.meta.avatar || '🤖'}</span>
              <div className="min-w-0">
                <h3 className="font-semibold text-gray-900 truncate">{expert.display_name}</h3>
                <p className="text-sm text-gray-500 truncate">{expert.name}</p>
              </div>
            </div>
            <p className="text-sm text-gray-600 mb-4 line-clamp-2">{expert.description}</p>
            <div className="flex gap-2">
              {!isExpertInstalled(expert.name) ? (
                <button
                  onClick={() => handleInstall(expert.name)}
                  disabled={operating === expert.name}
                  className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                  {operating === expert.name ? '安装中...' : '安装'}
                </button>
              ) : (
                <>
                  <button
                    onClick={() => handleCreateAgent(expert.name)}
                    className="px-4 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700"
                  >
                    创建对话
                  </button>
                  <button
                    onClick={() => handleUninstall(expert.name)}
                    disabled={operating === expert.name}
                    className="px-4 py-1.5 text-sm bg-gray-200 text-gray-700 rounded-lg hover:bg-red-100 hover:text-red-600 disabled:opacity-50"
                  >
                    {operating === expert.name ? '卸载中...' : '卸载'}
                  </button>
                </>
              )}
              {expert.meta.tags?.length > 0 && (
                <div className="flex items-center gap-1 ml-auto">
                  {expert.meta.tags.slice(0, 3).map(tag => (
                    <span key={tag} className="text-xs px-2 py-0.5 bg-gray-100 text-gray-500 rounded">{tag}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
