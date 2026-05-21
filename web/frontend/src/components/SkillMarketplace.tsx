import { useEffect, useRef, useState } from 'react';
import {
  fetchSkills, installSkillFromFile, installSkillFromZip,
  uninstallSkill, exportSkill,
  type SkillListItem,
} from '../api/client';

export default function SkillMarketplace() {
  const [skills, setSkills] = useState<SkillListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [operating, setOperating] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [error, setError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { load(); }, []);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const data = await fetchSkills();
      setSkills(data);
    } catch (e) {
      setError('加载技能列表失败');
    }
    setLoading(false);
  }

  async function handleSearch() {
    setLoading(true);
    try {
      const data = await fetchSkills(searchQuery || undefined);
      setSkills(data);
    } catch {
      setError('搜索失败');
    }
    setLoading(false);
  }

  async function handleInstallFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setOperating('__upload__');
    setError('');
    try {
      await installSkillFromFile(file);
      await load();
    } catch (err: any) {
      setError(err.message || '安装失败');
    }
    setOperating(null);
    // 重置 input 以便重复选择同一文件
    e.target.value = '';
  }

  async function handleInstallZip(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setOperating('__upload__');
    setError('');
    try {
      await installSkillFromZip(file);
      await load();
    } catch (err: any) {
      setError(err.message || '安装失败');
    }
    setOperating(null);
    e.target.value = '';
  }

  async function handleUninstall(name: string) {
    setOperating(name);
    setError('');
    try {
      await uninstallSkill(name);
      await load();
    } catch (err: any) {
      setError(err.message || '卸载失败');
    }
    setOperating(null);
  }

  async function handleExport(name: string) {
    try {
      const blob = await exportSkill(name);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${name}-SKILL.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError('导出失败');
    }
  }

  if (loading && skills.length === 0) {
    return <div className="p-6 text-gray-500">加载中...</div>;
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h2 className="text-2xl font-bold text-gray-800 mb-6">技能市场</h2>

      {/* 搜索 + 上传 */}
      <div className="flex items-center gap-3 mb-6">
        <input
          type="text"
          placeholder="搜索技能..."
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          className="flex-1 px-4 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
        <button
          onClick={handleSearch}
          className="px-4 py-2 text-sm bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200"
        >
          搜索
        </button>

        {/* 隐藏的文件输入，用 inline style 确保不可见 */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".md"
          style={{ display: 'none' }}
          onChange={handleInstallFile}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={operating === '__upload__'}
          className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          上传 SKILL.md
        </button>

        <input
          ref={zipInputRef}
          type="file"
          accept=".zip"
          style={{ display: 'none' }}
          onChange={handleInstallZip}
        />
        <button
          onClick={() => zipInputRef.current?.click()}
          disabled={operating === '__upload__'}
          className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          上传 ZIP
        </button>
      </div>

      {error && (
        <div className="mb-4 px-4 py-2 bg-red-50 text-red-700 rounded-lg text-sm">{error}</div>
      )}

      {/* 技能卡片网格 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {skills.map(skill => (
          <div
            key={skill.name}
            className="bg-white rounded-xl border p-5 hover:shadow-md transition-shadow"
          >
            <div className="flex items-start gap-3 mb-3">
              <div className="min-w-0 flex-1">
                <h3 className="font-semibold text-gray-900 truncate">{skill.name}</h3>
                <p className="text-sm text-gray-500 truncate">v{skill.version}</p>
              </div>
              <span
                className={`text-xs px-2 py-0.5 rounded ${
                  skill.source === 'bundled'
                    ? 'bg-purple-100 text-purple-600'
                    : 'bg-green-100 text-green-600'
                }`}
              >
                {skill.source === 'bundled' ? '内置' : '已安装'}
              </span>
            </div>

            <p className="text-sm text-gray-600 mb-3 line-clamp-2">{skill.description}</p>

            {skill.tools.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-3">
                {skill.tools.map(tool => (
                  <span key={tool} className="text-xs px-2 py-0.5 bg-gray-100 text-gray-500 rounded">
                    {tool}
                  </span>
                ))}
              </div>
            )}

            {skill.category && (
              <div className="mb-3">
                <span className="text-xs px-2 py-0.5 bg-blue-50 text-blue-500 rounded">
                  {skill.category}
                </span>
              </div>
            )}

            <div className="flex gap-2">
              <button
                onClick={() => handleExport(skill.name)}
                className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200"
              >
                导出
              </button>
              {skill.source !== 'bundled' && (
                <button
                  onClick={() => handleUninstall(skill.name)}
                  disabled={operating === skill.name}
                  className="px-3 py-1.5 text-sm bg-gray-200 text-gray-700 rounded-lg hover:bg-red-100 hover:text-red-600 disabled:opacity-50"
                >
                  {operating === skill.name ? '卸载中...' : '卸载'}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {skills.length === 0 && !loading && (
        <div className="text-center text-gray-400 mt-12">暂无技能，上传 SKILL.md 或 ZIP 安装</div>
      )}
    </div>
  );
}
