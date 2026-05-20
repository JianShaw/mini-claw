import { BrowserRouter, Routes, Route, NavLink, Navigate, useNavigate, useParams } from 'react-router-dom';
import { useState } from 'react';
import ExpertMarketplace from './components/ExpertMarketplace';
import ConversationList from './components/ConversationList';
import ChatWindow from './components/ChatWindow';
import type { Agent } from './api/client';

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen bg-gray-50">
        {/* Sidebar */}
        <div className="w-64 bg-white border-r flex flex-col">
          <div className="p-4 border-b">
            <h1 className="text-xl font-bold text-gray-800">Mini Claw</h1>
          </div>
          <nav className="flex-1 p-2">
            <NavLink
              to="/conversations"
              className={({ isActive }) =>
                `block px-3 py-2 rounded-lg mb-1 ${isActive ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-100'}`
              }
            >
              对话
            </NavLink>
            <NavLink
              to="/experts"
              className={({ isActive }) =>
                `block px-3 py-2 rounded-lg mb-1 ${isActive ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-100'}`
              }
            >
              专家广场
            </NavLink>
          </nav>
        </div>

        {/* Main content */}
        <div className="flex-1 flex flex-col min-w-0">
          <Routes>
            <Route path="/" element={<Navigate to="/conversations" replace />} />
            <Route path="/experts" element={<ExpertMarketplace />} />
            <Route path="/conversations" element={<ConversationListWithNav />} />
            <Route path="/conversations/:sessionId" element={<ChatWindowWithNav />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  );
}

/** ConversationList 包装：创建对话后导航到聊天页。 */
function ConversationListWithNav() {
  const navigate = useNavigate();
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <ConversationList
      key={refreshKey}
      onSelect={(conv) => navigate(`/conversations/${conv.session_id}`)}
      onConversationCreated={(sessionId) => {
        setRefreshKey(k => k + 1);
        navigate(`/conversations/${sessionId}`);
      }}
    />
  );
}

/** ChatWindow 包装：从 URL 取 sessionId，返回时导航回列表。 */
function ChatWindowWithNav() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();

  if (!sessionId) return <Navigate to="/conversations" replace />;

  return (
    <ChatWindow
      sessionId={sessionId}
      onBack={() => navigate('/conversations')}
    />
  );
}
