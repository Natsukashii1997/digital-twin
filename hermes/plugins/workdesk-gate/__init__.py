"""亚古兽 · 工具拦截（#142）。

1. 他人只能用安全工具：飞书等消息渠道上发起人不是常浩时，只放行 SAFE_TOOLS 里的工具；终端、文件、
   代码执行、浏览器、电脑操作、定时任务、派生、记忆、历史会话、Vault 和其他 MCP 一律拦下。
   常浩本人不受限；本机命令行和定时任务（非消息渠道）不受限。
2. 牛马之家（workdesk MCP）只在常浩本人的飞书私聊里可用；放行时把飞书发送人写进参数 _caller
   （覆盖模型自己填的），牛马之家据此记录提问人。
拿不到会话信息时按拦截处理。
"""

# 常浩在亚古兽应用下的两个 ID：open_id，以及飞书推送带上时 Hermes 优先使用的企业 user_id（2026-11-10 起）
OWNER_IDS = frozenset({"ou_6fac84a8164f84c0c591a9b3a98b462c", "e9cgcf77"})
PREFIXES = ("mcp__workdesk__", "mcp_workdesk_")  # 实际名字是双下划线（mcp__workdesk__project_list）；单下划线是文档写法，一并拦
BLOCKED = "牛马之家的数据目前只在常浩本人的飞书私聊里提供，这里不能读取。请如实告诉提问人，不要编造数据。"
# 他人可用：提问、读飞书文档、文档评论、网页搜索 / 摘取、查看技能、生图、语音、待办
SAFE_TOOLS = frozenset({
    "clarify", "feishu_doc_read", "feishu_drive_add_comment", "feishu_drive_list_comments",
    "feishu_drive_list_comment_replies", "feishu_drive_reply_comment", "web_search", "web_extract",
    "skill_view", "skills_list", "image_generate", "text_to_speech", "todo_list",
})
OWNER_ONLY = "这个工具只有常浩本人能让我用。请如实告诉提问人：这件事需要常浩本人来发起。"
# 非消息渠道：本机命令行、桌面、定时任务等（参照 Hermes gateway.session_context.NON_MESSAGING_SESSION_SURFACES，另加 cron）
LOCAL_SURFACES = frozenset({
    "", "api_server", "cli", "codex", "desktop", "gateway", "kanban", "local",
    "msgraph_webhook", "tool", "tui", "webhook", "cron",
})


def _session():
    from gateway.session_context import get_session_env
    return {k: get_session_env(f"HERMES_SESSION_{k.upper()}", "") or "" for k in ("platform", "chat_type", "user_id", "chat_id")}


def _gate(tool_name="", args=None, **kwargs):
    name = str(tool_name)
    is_workdesk = name.startswith(PREFIXES)
    try:
        s = _session()
    except Exception:
        if is_workdesk:
            return {"action": "block", "message": BLOCKED}
        return None if name in SAFE_TOOLS else {"action": "block", "message": OWNER_ONLY}
    platform = s["platform"].strip().lower()
    owner = s["user_id"] in OWNER_IDS
    if is_workdesk:
        if platform != "feishu" or s["chat_type"] != "dm" or not owner:
            return {"action": "block", "message": BLOCKED}
        return {"action": "modify", "args": {"_caller": {"platform": s["platform"], "chatType": s["chat_type"],
                                                         "userId": s["user_id"], "chatId": s["chat_id"]}}}
    if owner or platform in LOCAL_SURFACES or name in SAFE_TOOLS:
        return None
    return {"action": "block", "message": OWNER_ONLY}


def register(ctx):
    ctx.register_hook("pre_tool_call", _gate)
