import { useEffect, useState } from 'react';
import {
  fetchTasks, fetchTaskHistory, createTask, updateTask,
  toggleTask, triggerTask, deleteTask, fetchAgents,
  type ScheduledTask, type TaskRunRecord, type TriggerConfig,
  type Agent,
} from '../api/client';

/** 任务创建/编辑表单数据。 */
interface TaskForm {
  name: string;
  description: string;
  triggerType: 'cron' | 'interval';
  cronExpression: string;
  intervalSeconds: number;
  agentId: string;
  prompt: string;
  enabled: boolean;
}

const EMPTY_FORM: TaskForm = {
  name: '',
  description: '',
  triggerType: 'cron',
  cronExpression: '0 9 * * *',
  intervalSeconds: 3600,
  agentId: '',
  prompt: '',
  enabled: true,
};

export default function TaskManager() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<TaskForm>(EMPTY_FORM);
  const [historyTask, setHistoryTask] = useState<string | null>(null);
  const [historyRecords, setHistoryRecords] = useState<TaskRunRecord[]>([]);
  const [operating, setOperating] = useState<string | null>(null);

  useEffect(() => { load(); }, []);

  async function load() {
    setLoading(true);
    const [taskData, agentData] = await Promise.all([fetchTasks(), fetchAgents()]);
    setTasks(taskData);
    setAgents(agentData);
    setLoading(false);
  }

  function getAgentName(agentId: string | null) {
    if (!agentId) return '-';
    return agents.find(a => a.id === agentId)?.name || agentId;
  }

  function openCreateForm() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setShowForm(true);
  }

  function openEditForm(task: ScheduledTask) {
    setEditing(task.name);
    setForm({
      name: task.name,
      description: task.description,
      triggerType: task.trigger.type,
      cronExpression: task.trigger.expression || '0 9 * * *',
      intervalSeconds: task.trigger.seconds || 3600,
      agentId: task.agent_id || '',
      prompt: task.prompt || '',
      enabled: task.enabled,
    });
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditing(null);
  }

  async function handleSubmit() {
    const trigger: TriggerConfig =
      form.triggerType === 'cron'
        ? { type: 'cron', expression: form.cronExpression }
        : { type: 'interval', seconds: form.intervalSeconds };

    if (editing) {
      await updateTask(editing, {
        description: form.description,
        trigger,
        prompt: form.prompt,
        enabled: form.enabled,
      });
    } else {
      await createTask({
        name: form.name,
        description: form.description,
        trigger,
        agent_id: form.agentId,
        prompt: form.prompt,
        enabled: form.enabled,
      });
    }
    closeForm();
    await load();
  }

  async function handleToggle(name: string, current: boolean) {
    await toggleTask(name, !current);
    await load();
  }

  async function handleTrigger(name: string) {
    setOperating(name);
    const result = await triggerTask(name);
    setOperating(null);
    if (!result.success) alert(`触发失败: ${result.error}`);
    await load();
  }

  async function handleDelete(name: string) {
    if (!confirm(`确定删除任务 "${name}"？`)) return;
    await deleteTask(name);
    await load();
  }

  async function handleShowHistory(name: string) {
    if (historyTask === name) {
      setHistoryTask(null);
      setHistoryRecords([]);
      return;
    }
    const records = await fetchTaskHistory(name);
    setHistoryTask(name);
    setHistoryRecords(records);
  }

  function formatTrigger(trigger: TriggerConfig): string {
    if (trigger.type === 'cron') return `Cron: ${trigger.expression}`;
    if (trigger.type === 'interval') {
      const h = Math.floor(trigger.seconds! / 3600);
      const m = Math.floor((trigger.seconds! % 3600) / 60);
      if (h > 0) return `间隔: ${h}h${m > 0 ? ` ${m}m` : ''}`;
      return `间隔: ${m > 0 ? `${m}m` : `${trigger.seconds}s`}`;
    }
    return '-';
  }

  if (loading) return <div className="p-6 text-gray-500">加载中...</div>;

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-gray-800">定时任务</h2>
        <button
          onClick={openCreateForm}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium"
        >
          + 新建任务
        </button>
      </div>

      {/* 任务列表 */}
      {tasks.length === 0 ? (
        <div className="text-center py-12 text-gray-400">暂无定时任务</div>
      ) : (
        <div className="space-y-3">
          {tasks.map(task => (
            <div key={task.name} className="bg-white rounded-xl border p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 min-w-0">
                  <span
                    className={`inline-block w-2.5 h-2.5 rounded-full ${
                      task.enabled
                        ? task.is_running ? 'bg-yellow-400' : 'bg-green-500'
                        : 'bg-gray-300'
                    }`}
                  />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-900">{task.name}</span>
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        task.task_type === 'llm'
                          ? 'bg-blue-50 text-blue-600'
                          : 'bg-gray-100 text-gray-500'
                      }`}>
                        {task.task_type === 'llm' ? 'LLM' : '系统'}
                      </span>
                    </div>
                    <div className="text-sm text-gray-500 truncate">
                      {task.description || formatTrigger(task.trigger)}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {/* 上次执行结果 */}
                  {task.last_success !== null && (
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      task.last_success
                        ? 'bg-green-50 text-green-600'
                        : 'bg-red-50 text-red-600'
                    }`}>
                      {task.last_success ? '成功' : '失败'}
                    </span>
                  )}

                  <button
                    onClick={() => handleToggle(task.name, task.enabled)}
                    className={`px-3 py-1 text-xs rounded-lg border ${
                      task.enabled
                        ? 'border-green-300 text-green-600 hover:bg-green-50'
                        : 'border-gray-300 text-gray-500 hover:bg-gray-50'
                    }`}
                  >
                    {task.enabled ? '已启用' : '已禁用'}
                  </button>
                  <button
                    onClick={() => handleTrigger(task.name)}
                    disabled={operating === task.name}
                    className="px-3 py-1 text-xs rounded-lg border border-blue-300 text-blue-600 hover:bg-blue-50 disabled:opacity-50"
                  >
                    {operating === task.name ? '执行中...' : '手动触发'}
                  </button>
                  <button
                    onClick={() => handleShowHistory(task.name)}
                    className={`px-3 py-1 text-xs rounded-lg border ${
                      historyTask === task.name
                        ? 'border-purple-400 text-purple-600 bg-purple-50'
                        : 'border-gray-300 text-gray-500 hover:bg-gray-50'
                    }`}
                  >
                    历史
                  </button>
                  {task.task_type === 'llm' && (
                    <>
                      <button
                        onClick={() => openEditForm(task)}
                        className="px-3 py-1 text-xs rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => handleDelete(task.name)}
                        className="px-3 py-1 text-xs rounded-lg border border-red-300 text-red-500 hover:bg-red-50"
                      >
                        删除
                      </button>
                    </>
                  )}
                </div>
              </div>

              {/* 详情行 */}
              <div className="mt-2 flex gap-4 text-xs text-gray-400">
                <span>{formatTrigger(task.trigger)}</span>
                {task.agent_id && <span>专家: {getAgentName(task.agent_id)}</span>}
                {task.last_error && (
                  <span className="text-red-400">错误: {task.last_error}</span>
                )}
              </div>

              {/* 历史面板 */}
              {historyTask === task.name && historyRecords.length > 0 && (
                <div className="mt-3 border-t pt-3">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-gray-500">
                        <th className="text-left py-1">触发时间</th>
                        <th className="text-left py-1">完成时间</th>
                        <th className="text-left py-1">状态</th>
                        <th className="text-left py-1">消息</th>
                      </tr>
                    </thead>
                    <tbody>
                      {historyRecords.map((r, i) => (
                        <tr key={i} className="border-t border-gray-100">
                          <td className="py-1 text-gray-600">{new Date(r.triggered_at).toLocaleString()}</td>
                          <td className="py-1 text-gray-600">{new Date(r.completed_at).toLocaleString()}</td>
                          <td className={`py-1 ${r.success ? 'text-green-600' : 'text-red-500'}`}>
                            {r.success ? '成功' : '失败'}
                          </td>
                          <td className="py-1 text-gray-500 truncate max-w-xs">
                            {r.error || r.message}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {historyTask === task.name && historyRecords.length === 0 && (
                <div className="mt-3 border-t pt-2 text-xs text-gray-400">暂无执行记录</div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 创建/编辑表单 */}
      {showForm && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
             onClick={(e) => { if (e.target === e.currentTarget) closeForm(); }}>
          <div className="bg-white rounded-xl shadow-lg p-6 w-full max-w-lg">
            <h3 className="text-lg font-semibold text-gray-800 mb-4">
              {editing ? `编辑任务: ${editing}` : '新建定时任务'}
            </h3>

            <div className="space-y-4">
              {!editing && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">任务名称</label>
                  <input
                    value={form.name}
                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                    className="w-full px-3 py-2 border rounded-lg text-sm"
                    placeholder="例如: morning_greeting"
                  />
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">描述</label>
                <input
                  value={form.description}
                  onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
                  placeholder="任务描述"
                />
              </div>

              {!editing && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">选择专家</label>
                  <select
                    value={form.agentId}
                    onChange={e => setForm(f => ({ ...f, agentId: e.target.value }))}
                    className="w-full px-3 py-2 border rounded-lg text-sm"
                  >
                    <option value="">-- 请选择专家 --</option>
                    {agents.filter(a => a.id !== 'default-agent').map(agent => (
                      <option key={agent.id} value={agent.id}>{agent.name}</option>
                    ))}
                  </select>
                  <p className="text-xs text-gray-400 mt-1">任务将自动创建专用推送会话</p>
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">触发类型</label>
                <select
                  value={form.triggerType}
                  onChange={e => setForm(f => ({ ...f, triggerType: e.target.value as 'cron' | 'interval' }))}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
                >
                  <option value="cron">Cron 表达式</option>
                  <option value="interval">固定间隔</option>
                </select>
              </div>

              {form.triggerType === 'cron' ? (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Cron 表达式</label>
                  <input
                    value={form.cronExpression}
                    onChange={e => setForm(f => ({ ...f, cronExpression: e.target.value }))}
                    className="w-full px-3 py-2 border rounded-lg text-sm font-mono"
                    placeholder="0 9 * * *"
                  />
                  <p className="text-xs text-gray-400 mt-1">格式: 分 时 日 月 周（5 字段）</p>
                </div>
              ) : (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">间隔（秒）</label>
                  <input
                    type="number"
                    value={form.intervalSeconds}
                    onChange={e => setForm(f => ({ ...f, intervalSeconds: parseInt(e.target.value) || 60 }))}
                    className="w-full px-3 py-2 border rounded-lg text-sm"
                    min={1}
                  />
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Prompt</label>
                <textarea
                  value={form.prompt}
                  onChange={e => setForm(f => ({ ...f, prompt: e.target.value }))}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
                  rows={3}
                  placeholder="发送给 Agent 的提示词"
                />
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={closeForm}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
              >
                取消
              </button>
              <button
                onClick={handleSubmit}
                disabled={(!form.name && !editing) || !form.prompt || (!editing && !form.agentId)}
                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {editing ? '保存' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
