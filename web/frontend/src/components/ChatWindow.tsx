import { useEffect, useRef, useState } from 'react';
import { streamChat, fetchConversation, type Message } from '../api/client';

interface Props {
  sessionId: string;
  onBack: () => void;
}

export default function ChatWindow({ sessionId, onBack }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    loadMessages();
  }, [sessionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamText]);

  async function loadMessages() {
    const conv = await fetchConversation(sessionId);
    setMessages(conv.messages || []);
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || streaming) return;

    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: text }]);
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
      // 流结束：先写入 messages，再清空 streamText，同一帧渲染
      setMessages(msgs => [...msgs, { role: 'assistant', content: full }]);
      setStreamText('');
    } catch (err) {
      setStreamText(prev => {
        if (prev) {
          setMessages(msgs => [...msgs, { role: 'assistant', content: prev }]);
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

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[70%] px-4 py-2.5 rounded-2xl text-sm whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : 'bg-white border text-gray-800'
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}
        {streamText && (
          <div className="flex justify-start">
            <div className="max-w-[70%] px-4 py-2.5 rounded-2xl text-sm bg-white border text-gray-800 whitespace-pre-wrap">
              {streamText}
              <span className="animate-pulse">▌</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-4 py-3 border-t bg-white">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()}
            placeholder="输入消息..."
            disabled={streaming}
            className="flex-1 px-4 py-2 border rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={streaming || !input.trim()}
            className="px-5 py-2 bg-blue-600 text-white rounded-xl text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
