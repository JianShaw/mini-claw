import { useEffect, useMemo, useRef, useState } from 'react';
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso';
import { streamChat, fetchConversation, type Message } from '../api/client';
import { formatMessageTime, isDifferentDay, formatDateSeparator } from '../utils/time';

interface Props {
  sessionId: string;
  onBack: () => void;
}

// 统一渲染列表项
type RenderItem =
  | { kind: 'message'; index: number; msg: Message }
  | { kind: 'streaming_text'; content: string }
  | { kind: 'streaming_thinking' };

export default function ChatWindow({ sessionId, onBack }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState('');
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const initialScrollDone = useRef(false);

  useEffect(() => {
    initialScrollDone.current = false;
    loadMessages();
  }, [sessionId]);

  // 首次加载消息后滚到底部（initialTopMostItemIndex 对异步数据不生效）
  useEffect(() => {
    if (messages.length > 0 && !initialScrollDone.current) {
      initialScrollDone.current = true;
      requestAnimationFrame(() => {
        virtuosoRef.current?.scrollToIndex({
          index: messages.length - 1,
          behavior: 'auto',
        });
      });
    }
  }, [messages]);

  async function loadMessages() {
    const conv = await fetchConversation(sessionId);
    setMessages(conv.messages || []);
  }

  // 构建统一渲染列表
  const renderItems: RenderItem[] = useMemo(() => {
    const items: RenderItem[] = messages.map((msg, i) => ({ kind: 'message' as const, index: i, msg }));
    if (streaming && streamText) {
      items.push({ kind: 'streaming_text', content: streamText });
    } else if (streaming) {
      items.push({ kind: 'streaming_thinking' });
    }
    return items;
  }, [messages, streaming, streamText]);

  // 获取前一条消息（用于日期分隔线判断）
  function getPrevMessage(index: number): Message | null {
    // 在 renderItems 中找前一个 kind=message 的项
    for (let i = index - 1; i >= 0; i--) {
      if (renderItems[i].kind === 'message') return (renderItems[i] as { msg: Message }).msg;
    }
    return null;
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || streaming) return;

    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: text, ts: Date.now() }]);
    setStreaming(true);
    setStreamText('');

    try {
      let full = '';
      for await (const chunk of streamChat(sessionId, text)) {
        if (chunk.type === 'content') {
          full += chunk.text;
          setStreamText(full);
        } else if (chunk.type === 'error') {
          full += `[错误] ${chunk.text}`;
          setStreamText(full);
        }
      }
      setMessages(prev => [...prev, { role: 'assistant', content: full, ts: Date.now() }]);
      setStreamText('');
    } catch {
      setStreamText(prev => {
        if (prev) {
          setMessages(msgs => [...msgs, { role: 'assistant', content: prev, ts: Date.now() }]);
        }
        return '';
      });
    } finally {
      setStreaming(false);
    }
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b bg-white">
        <button onClick={onBack} className="text-gray-400 hover:text-gray-700">
          ← 返回
        </button>
        <h2 className="font-medium text-gray-700 truncate">
          对话 {sessionId.slice(0, 12)}...
        </h2>
      </div>

      {/* Messages — 虚拟列表 */}
      <div className="flex-1 relative">
        <Virtuoso
          ref={virtuosoRef}
          data={renderItems}
          followOutput="smooth"
          initialTopMostItemIndex={Math.max(0, renderItems.length - 1)}
          atBottomStateChange={setShowScrollBtn}
          atBottomThreshold={100}
          itemContent={(index, item) => (
            <RenderItemContent
              item={item}
              prevMsg={getPrevMessage(index)}
            />
          )}
          components={{
            Header: () => <div className="h-4" />,
            Footer: () => <div className="h-4" />,
          }}
        />
        {/* 回到底部按钮 */}
        {showScrollBtn && (
          <button
            onClick={() => virtuosoRef.current?.scrollToIndex({ index: renderItems.length - 1, behavior: 'smooth' })}
            className="absolute bottom-4 right-6 w-10 h-10 bg-white border rounded-full shadow-lg flex items-center justify-center text-gray-500 hover:text-gray-700 transition-colors"
          >
            ↓
          </button>
        )}
      </div>

      {/* Input */}
      <div className="px-4 py-3 border-t bg-white">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()}
            placeholder={streaming ? 'AI 正在回复...' : '输入消息...'}
            disabled={streaming}
            className="flex-1 px-4 py-2 border rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={streaming || !input.trim()}
            className={`w-10 h-10 flex items-center justify-center rounded-xl text-sm transition-colors ${
              streaming
                ? 'bg-gray-300 text-gray-500'
                : 'bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50'
            }`}
          >
            {streaming ? (
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

// --- 子组件 ---

function RenderItemContent({ item, prevMsg }: { item: RenderItem; prevMsg: Message | null }) {
  switch (item.kind) {
    case 'message':
      return <MessageBubble msg={item.msg} prevMsg={prevMsg} />;
    case 'streaming_text':
      return (
        <div className="px-4 mb-4">
          <div className="flex justify-start">
            <div className="max-w-[70%] px-4 py-2.5 rounded-2xl text-sm bg-white border text-gray-800 whitespace-pre-wrap">
              {item.content}
              <span className="animate-pulse">▌</span>
            </div>
          </div>
        </div>
      );
    case 'streaming_thinking':
      return (
        <div className="px-4 mb-4">
          <div className="flex justify-start">
            <div className="px-4 py-2.5 rounded-2xl text-sm bg-white border text-gray-400 flex items-center gap-1.5">
              <span className="flex gap-0.5">
                <span className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-1.5 h-1.5 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </span>
              思考中...
            </div>
          </div>
        </div>
      );
  }
}

function MessageBubble({ msg, prevMsg }: { msg: Message; prevMsg: Message | null }) {
  const isUser = msg.role === 'user';
  const showDateSep = prevMsg ? isDifferentDay(prevMsg.ts, msg.ts) : false;
  const timeStr = formatMessageTime(msg.ts);

  return (
    <div className="px-4 mb-4">
      {/* 日期分隔线 */}
      {showDateSep && msg.ts && (
        <div className="flex justify-center py-2 mb-2">
          <span className="text-xs text-gray-400 bg-gray-50 px-3 py-1 rounded-full">
            {formatDateSeparator(msg.ts)}
          </span>
        </div>
      )}
      <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
        <div className={`max-w-[70%] ${isUser ? 'items-end' : 'items-start'} flex flex-col`}>
          <div
            className={`px-4 py-2.5 rounded-2xl text-sm whitespace-pre-wrap ${
              isUser ? 'bg-blue-600 text-white' : 'bg-white border text-gray-800'
            }`}
          >
            {msg.content}
          </div>
          {timeStr && (
            <span className={`text-xs text-gray-400 mt-1 ${isUser ? 'mr-1' : 'ml-1'}`}>
              {timeStr}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
