"""亚古兽 · 牛马之家（workdesk MCP）拦截。

牛马之家的工具只在常浩本人的飞书私聊里可用；群聊、文档评论、其他人一律拦下。
放行时把飞书发送人写进参数 _caller（覆盖模型自己填的），牛马之家据此记录提问人。
拿不到会话信息时按拦截处理。
"""

OWNER_OPEN_ID = "ou_6fac84a8164f84c0c591a9b3a98b462c"  # 常浩在亚古兽应用下的 open_id
PREFIXES = ("mcp__workdesk__", "mcp_workdesk_")  # 实际名字是双下划线（mcp__workdesk__project_list）；单下划线是文档写法，一并拦
BLOCKED = "牛马之家的数据目前只在常浩本人的飞书私聊里提供，这里不能读取。请如实告诉提问人，不要编造数据。"


def _gate(tool_name="", args=None, **kwargs):
    if not str(tool_name).startswith(PREFIXES):
        return None
    try:
        from gateway.session_context import get_session_env
        platform = get_session_env("HERMES_SESSION_PLATFORM", "")
        chat_type = get_session_env("HERMES_SESSION_CHAT_TYPE", "")
        user_id = get_session_env("HERMES_SESSION_USER_ID", "")
        chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "")
    except Exception:
        return {"action": "block", "message": BLOCKED}
    if platform != "feishu" or chat_type != "dm" or user_id != OWNER_OPEN_ID:
        return {"action": "block", "message": BLOCKED}
    return {"action": "modify", "args": {"_caller": {"platform": platform, "chatType": chat_type, "userId": user_id, "chatId": chat_id}}}


def register(ctx):
    ctx.register_hook("pre_tool_call", _gate)
