"""JsonlSessionStore 测试：验证 JSONL 文件持久化存储的完整行为。"""

from __future__ import annotations

import json

from claw.session import JsonlSessionStore, create_session
from claw.types import ChatMessage, InboundMessage


def _msg(**overrides: object) -> InboundMessage:
    defaults = dict(
        channel="local",
        account_id="app",
        peer_id="user",
        sender_id="user",
        message_id="1",
        text="hi",
        timestamp=0,
        message_type="text",
        raw=None,
    )
    defaults.update(overrides)
    return InboundMessage(**defaults)  # type: ignore[arg-type]


async def test_save_and_get_by_id(tmp_path) -> None:
    """save 后 get_by_id 应返回包含完整 history 的 session。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    msg = _msg(peer_id="u1")
    session = create_session(msg)
    session.history.append(ChatMessage(role="user", content="hello"))
    await store.save(session)

    loaded = await store.get_by_id(session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert len(loaded.history) == 1
    assert loaded.history[0].content == "hello"


async def test_save_appends_new_messages_only(tmp_path) -> None:
    """多次 save 只追加新消息到 JSONL。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))

    session.history.append(ChatMessage(role="user", content="msg1"))
    await store.save(session)

    session.history.append(ChatMessage(role="assistant", content="reply1"))
    await store.save(session)

    loaded = await store.get_by_id(session.session_id)
    assert len(loaded.history) == 2
    assert loaded.history[0].content == "msg1"
    assert loaded.history[1].content == "reply1"

    # 验证 JSONL 文件只有 2 行
    jsonl_path = tmp_path / f"{session.session_id}.jsonl"
    lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


async def test_get_active_returns_active_session(tmp_path) -> None:
    """get_active 应返回当前活跃的 session。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    session.history.append(ChatMessage(role="user", content="hi"))
    await store.save(session)

    active = await store.get_active("local:app:u1")
    assert active is not None
    assert active.session_id == session.session_id
    assert len(active.history) == 1


async def test_set_active_switches_session(tmp_path) -> None:
    """set_active 切换活跃 session 后 get_active 返回新的。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    s1 = create_session(_msg(peer_id="u1"))
    s2 = create_session(_msg(peer_id="u1"))
    await store.save(s1)
    await store.save(s2)

    await store.set_active("local:app:u1", s1.session_id)
    active = await store.get_active("local:app:u1")
    assert active.session_id == s1.session_id


async def test_list_sessions(tmp_path) -> None:
    """list_sessions 列出 peer 下的所有 session。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    s1 = create_session(_msg(peer_id="u1"))
    s2 = create_session(_msg(peer_id="u1"))
    await store.save(s1)
    await store.save(s2)

    sessions = await store.list_sessions("local:app:u1")
    assert len(sessions) == 2
    ids = {s.session_id for s in sessions}
    assert ids == {s1.session_id, s2.session_id}


async def test_list_sessions_empty(tmp_path) -> None:
    """没有 session 时 list_sessions 返回空列表。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    sessions = await store.list_sessions("local:app:user")
    assert sessions == []


async def test_delete_removes_session(tmp_path) -> None:
    """delete 后 session 不可查，JSONL 文件和 index 条目均删除。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    session.history.append(ChatMessage(role="user", content="hi"))
    await store.save(session)

    jsonl_path = tmp_path / f"{session.session_id}.jsonl"
    assert jsonl_path.exists()

    await store.delete(session.session_id)
    assert await store.get_by_id(session.session_id) is None
    assert not jsonl_path.exists()


async def test_delete_active_switches_to_another(tmp_path) -> None:
    """删除活跃 session 时自动切换到同 peer 的另一个。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    s1 = create_session(_msg(peer_id="u1"))
    s2 = create_session(_msg(peer_id="u1"))
    await store.save(s1)
    await store.save(s2)

    # s2 是 active
    await store.delete(s2.session_id)
    active = await store.get_active("local:app:u1")
    assert active is not None
    assert active.session_id == s1.session_id


async def test_delete_last_session_clears_active(tmp_path) -> None:
    """删除 peer 下唯一 session 后 get_active 返回 None。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    await store.save(session)
    await store.delete(session.session_id)
    assert await store.get_active("local:app:u1") is None


async def test_get_returns_active(tmp_path) -> None:
    """get(peer_key) 是 get_active 的别名。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    await store.save(session)

    result = await store.get("local:app:u1")
    assert result is not None
    assert result.session_id == session.session_id


async def test_get_returns_none_when_empty(tmp_path) -> None:
    """没有 session 时 get 返回 None。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    assert await store.get("local:app:user") is None


async def test_restart_preserves_data(tmp_path) -> None:
    """进程重启（新建 JsonlSessionStore 实例）后数据仍可加载。"""
    store1 = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    session.history.append(ChatMessage(role="user", content="hello"))
    await store1.save(session)

    # 模拟重启：新建实例
    store2 = JsonlSessionStore(data_dir=tmp_path)
    loaded = await store2.get_by_id(session.session_id)
    assert loaded is not None
    assert len(loaded.history) == 1
    assert loaded.history[0].content == "hello"

    # 追加新消息不会重复写入已有消息
    loaded.history.append(ChatMessage(role="assistant", content="hi"))
    await store2.save(loaded)

    jsonl_path = tmp_path / f"{session.session_id}.jsonl"
    lines = [l for l in jsonl_path.read_text(encoding="utf-8").strip().split("\n") if l]
    assert len(lines) == 2


async def test_summary_stored_in_index(tmp_path) -> None:
    """compact 后 summary 应存入 index.json，JSONL 文件不变。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    session.history.append(ChatMessage(role="user", content="hello"))
    await store.save(session)

    # 模拟 compact：设置 summary，清空 history
    session.summary = "用户说了 hello"
    original_history_len = len(session.history)
    session.history = []
    await store.save(session)

    # 从 index.json 验证 summary
    index_data = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    peer_entry = index_data["local:app:u1"]
    assert peer_entry["sessions"][session.session_id]["summary"] == "用户说了 hello"

    # JSONL 文件仍保留原始记录
    jsonl_path = tmp_path / f"{session.session_id}.jsonl"
    lines = [l for l in jsonl_path.read_text(encoding="utf-8").strip().split("\n") if l]
    assert len(lines) == original_history_len


async def test_chat_message_ts_written_to_jsonl(tmp_path) -> None:
    """ChatMessage 带 ts 时应写入 JSONL 记录。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    session.history.append(ChatMessage(role="user", content="hello", ts=1747000000))
    await store.save(session)

    jsonl_path = tmp_path / f"{session.session_id}.jsonl"
    record = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
    assert record["ts"] == 1747000000


# --- history_offset 相关测试 ---


async def test_history_offset_prevents_old_messages_reappearing(tmp_path) -> None:
    """压缩后重载 session 不应包含已压缩的旧消息。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    # 写入 6 条消息（3 轮对话）
    for i in range(3):
        session.history.append(ChatMessage(role="user", content=f"q{i}"))
        session.history.append(ChatMessage(role="assistant", content=f"a{i}"))
    await store.save(session)

    # 模拟压缩：保留最后 2 条，前 4 条被压缩
    session.history_offset = 4
    session.summary = "Summary of q0,a0,q1,a1"
    session.history = session.history[4:]  # [q2, a2]
    await store.save(session)

    # 重载：应只有 2 条消息
    loaded = await store.get_active("local:app:u1")
    assert loaded is not None
    assert len(loaded.history) == 2
    assert loaded.history[0].content == "q2"
    assert loaded.history[1].content == "a2"
    assert loaded.summary == "Summary of q0,a0,q1,a1"
    assert loaded.history_offset == 4


async def test_history_offset_append_new_messages_after_compact(tmp_path) -> None:
    """压缩后追加新消息不会重复写入旧消息。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    for i in range(3):
        session.history.append(ChatMessage(role="user", content=f"q{i}"))
        session.history.append(ChatMessage(role="assistant", content=f"a{i}"))
    await store.save(session)

    # 模拟压缩
    session.history_offset = 4
    session.summary = "old summary"
    session.history = session.history[4:]  # [q2, a2]
    await store.save(session)

    # 追加新消息
    session.history.append(ChatMessage(role="user", content="q3"))
    session.history.append(ChatMessage(role="assistant", content="a3"))
    await store.save(session)

    # 重载：应只有 4 条消息（q2,a2,q3,a3）
    loaded = await store.get_active("local:app:u1")
    assert len(loaded.history) == 4
    assert loaded.history[2].content == "q3"
    assert loaded.history[3].content == "a3"

    # JSONL 应有 8 条（原始 6 + 新增 2），不会重复
    jsonl_path = tmp_path / f"{session.session_id}.jsonl"
    lines = [l for l in jsonl_path.read_text(encoding="utf-8").strip().split("\n") if l]
    assert len(lines) == 8


async def test_history_offset_multiple_compacts(tmp_path) -> None:
    """多次压缩 offset 累积正确。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    for i in range(5):
        session.history.append(ChatMessage(role="user", content=f"q{i}"))
        session.history.append(ChatMessage(role="assistant", content=f"a{i}"))
    await store.save(session)

    # 第一次压缩：保留后 4 条，offset = 6
    session.history_offset = 6
    session.summary = "first"
    session.history = session.history[6:]
    await store.save(session)

    # 第二次压缩：保留后 2 条，offset = 6 + 2 = 8
    session.history_offset = 8
    session.summary = "second"
    session.history = session.history[2:]
    await store.save(session)

    loaded = await store.get_active("local:app:u1")
    assert loaded.history_offset == 8
    assert len(loaded.history) == 2  # 只剩最后 2 条


async def test_history_offset_backward_compatible(tmp_path) -> None:
    """旧 index.json 没有 history_offset 时默认为 0，行为不变。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    session.history.append(ChatMessage(role="user", content="hi"))
    await store.save(session)

    # 手动删除 index 中的 history_offset（模拟旧版数据）
    index_path = tmp_path / "index.json"
    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    meta = index_data["local:app:u1"]["sessions"][session.session_id]
    meta.pop("history_offset", None)
    index_path.write_text(json.dumps(index_data, ensure_ascii=False), encoding="utf-8")

    # 重载：应正常工作，offset 默认 0
    store2 = JsonlSessionStore(data_dir=tmp_path)
    loaded = await store2.get_active("local:app:u1")
    assert loaded is not None
    assert len(loaded.history) == 1
    assert loaded.history_offset == 0


async def test_history_offset_saved_count_missing_no_duplicates(tmp_path) -> None:
    """_saved_count 未初始化时 save 不会重复追写旧消息。"""
    store = JsonlSessionStore(data_dir=tmp_path)
    session = create_session(_msg(peer_id="u1"))
    session.history.append(ChatMessage(role="user", content="hi"))
    await store.save(session)

    # 手动设置 offset 但不经过 get（_saved_count 未设）
    # 模拟：新 store 实例，手动构造 session 带 offset
    store2 = JsonlSessionStore(data_dir=tmp_path)
    # 直接 save 一个带 offset 的 session（不经过 get_active）
    loaded = await store2.get_by_id(session.session_id)
    assert loaded is not None
    loaded.history_offset = 1
    loaded.summary = "compressed"
    loaded.history = []
    await store2.save(loaded)

    # JSONL 应只有 1 行，不会重复
    jsonl_path = tmp_path / f"{session.session_id}.jsonl"
    lines = [l for l in jsonl_path.read_text(encoding="utf-8").strip().split("\n") if l]
    assert len(lines) == 1

