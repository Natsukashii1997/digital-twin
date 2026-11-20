#!/usr/bin/env python3
"""中台卡片的飞书样子（#195 批 1「飞书拍板」）：和发送通道无关的渲染模块。

只做一件事：把中台的一张卡片（kernel/query.ts 的 cardView：id、version、kind ask|notice、state open|decided|done|closed、
title、text、options、recommend、choice、answer、by、result、rule、createdAt、decidedAt、closedAt）画成飞书卡片 JSON 2.0。
不发消息、不读写任何库、不认令牌、不调 AI。谁发（现有桥接 autoloop_feishu 以猛鬼兽机器人发，以后门卫机器人在 dt-gate 发）、
按钮里放什么（令牌方案），都由调用方决定，所以两边能共用。

接口（给 autoloop_feishu / 门卫，PR 正文同样写了）：
  render(card, value_for, mark=None, secretary="秘书", hint=None) -> dict
      value_for(i) -> dict：第 i 个选项按钮的回调值（桥接传 {"autoloop": 令牌, "o": i, "v": 版本}，门卫传自己的）。
      只在卡片开着时调用；同一张卡同一版本，每次渲染出来的按钮值完全一样（重复点带的是同一个值，由收点击的一方去重）。
      结束了的卡（decided / done / closed）不画任何按钮。
      样式是定稿的丙版「推荐优先」（常浩 2026-11-20 从三版里选定，登记在 docs/DECIDED.md）：开着时大字写建议、一个通栏大按钮
      「✅ 按推荐：…」，其余选项收在「不同意？其他选择」折叠面板里；没有推荐（或是通知卡）时所有选项平铺，不替常浩挑；
      结束后大字写结果，原问题收进「当时问的是什么」折叠面板。
      mark：页脚小字（如卡片令牌尾号，回读时认卡用）；
      secretary：秘书在 by 里的名字（同 autoloop_config.SECRETARY）；hint：开着时的「怎么回」提示，None 用默认。
  outcome(card, secretary="秘书") -> str | None   结束的卡写什么结果（开着的卡返回 None）
  parse_reply(text, card) -> int | None           引用卡片打字回答：认「1」「①」「按推荐」或选项原文，认不出返回 None。
      只给出「想选哪一项」，算不算批准由调用方定（ADR-015 修订 1：私聊打字只能发起提议，批准要门卫卡上的点击）。
  button_values(content) -> list[dict]            从渲染结果里取出全部按钮回调值（回读核对、测试用）
"""
import re

NUMS = "①②③④⑤⑥⑦⑧⑨⑩"
DEFAULT_HINT = "点按钮就算你定了，只能点一次。也可以引用这张卡片回一句，例如「1」「按推荐」或选项原文。"
COLOR = {"open": "indigo", "decided": "green", "done": "green", "closed": "grey"}


def is_open(card):
    return card.get("state") == "open"


def options(card):
    return [str(o) for o in (card.get("options") or [])]


def recommend(card):
    r = card.get("recommend")
    return r if type(r) is int and 0 <= r < len(options(card)) else None


def chosen(card):
    c, opts = card.get("choice"), options(card)
    if type(c) is int and 0 <= c < len(opts):
        return opts[c]
    return card.get("answer")


def who(card, secretary):
    return "秘书替你" if card.get("by") == secretary else "你"


def outcome(card, secretary="秘书"):
    """结束的卡写什么（开着的卡 None）。closed 对提问卡就是「不用定了」：中台关卡时写了原因（Agent 自己解决了、后来跑成了…）。"""
    state, pick = card.get("state"), chosen(card)
    if state == "decided":
        return f"{who(card, secretary)}选了「{pick}」，已记下，等 Agent 接着做。"
    if state == "done":
        return f"{who(card, secretary)}选了「{pick}」，" + (f"已办完：{card['result']}" if card.get("result") else "已办完。")
    if state == "closed":
        reason = card.get("result") or ""
        if card.get("kind") == "ask":
            return f"不用你定了{('：' + reason) if reason else '。'}"
        return reason or "已结束。"
    return None


# ---------- 积木 ----------
def md(text, **extra):
    return {"tag": "markdown", "content": text, **extra}


def grey(text):
    return md(f"<font color='grey'>{text}</font>", text_size="notation")


def button(label, value, kind="default", width=None, size=None):
    b = {"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": kind,
         "behaviors": [{"type": "callback", "value": value}]}
    if width:
        b["width"] = width
    if size:
        b["size"] = size
    return b


def panel(title, elements):
    """默认收起的折叠面板（写法同日报乙版定稿）。"""
    return {"tag": "collapsible_panel", "expanded": False, "background_color": "grey-50",
            "border": {"color": "grey", "corner_radius": "6px"}, "padding": "4px 10px 8px 10px", "vertical_spacing": "8px",
            "header": {"title": {"tag": "plain_text", "content": title}, "width": "fill", "vertical_align": "center",
                       "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "color": "grey", "size": "16px 16px"},
                       "icon_position": "right", "icon_expanded_angle": -180},
            "elements": elements}


def footer(card, mark, extra=""):
    bits = [f"卡片 #{card.get('id')} · 第 {card.get('version')} 版"]
    if card.get("rule"):
        bits.append(f"来源 {card['rule']}")
    if mark:
        bits.append(mark)
    return grey("｜".join(bits) + (f"｜{extra}" if extra else ""))


def card_json(header, elements):
    return {"schema": "2.0", "config": {"update_multi": True, "enable_forward": False}, "header": header,
            "body": {"elements": elements}}


def head(title, template):
    return {"title": {"tag": "plain_text", "content": title}, "template": template}


def buttons_open(card, value_for):
    """开着的卡能点的选项：提问卡每个选项一个，通知卡（中台给 ["已处理"]）一个。"""
    return [(i, label, value_for(i)) for i, label in enumerate(options(card))] if is_open(card) else []


# ---------- 定稿样式（丙 · 推荐优先，常浩 2026-11-20 选定）：一句建议 + 一个大按钮，其余选项收起 ----------
def render(card, value_for, mark=None, secretary="秘书", hint=None):
    """见文件头接口说明。"""
    hint = DEFAULT_HINT if hint is None else hint
    state, rec, opts, title = card.get("state"), recommend(card), options(card), card.get("title") or "中台卡片"
    els = []
    btns = buttons_open(card, value_for)
    explain = [md(card["text"])] if card.get("text") else []
    if btns and rec is not None and card.get("kind") == "ask":
        els.append(md(f"**我建议：{opts[rec]}**", text_size="heading"))
        els += explain
        i, label, value = btns[rec]
        els.append(button(f"✅ 按推荐：{label}", value, "primary_filled", width="fill", size="large"))
        others = [button(f"改选：{lb}", v, "default", width="fill") for j, lb, v in btns if j != rec]
        if others:
            els.append(panel("不同意？其他选择 · 展开", others))
        els.append(footer(card, mark, hint))
    elif btns:                                     # 没有推荐（或是通知卡）：所有选项平铺，不替常浩挑
        els += explain
        els += [button(label, value, "primary_filled" if card.get("kind") != "ask" else "default", width="fill")
                for _, label, value in btns]
        els.append(footer(card, mark, hint))
    else:
        icon = {"decided": "✅", "done": "🏁", "closed": "⏹"}.get(state, "•")
        followed = (state in ("decided", "done") and rec is not None and card.get("choice") == rec)
        dropped = state == "closed" and card.get("kind") == "ask"         # 提问卡被关：不用常浩定了，下面写原因
        headline = (f"已按推荐：{chosen(card)}" if followed else "这件事不用你定了" if dropped
                    else outcome(card, secretary) or "已结束。")
        els.append(md(f"**{icon} {headline}**", text_size="heading"))
        if dropped and card.get("result"):
            els.append(md(f"原因：{card['result']}"))
        elif followed:
            els.append(md(outcome(card, secretary)))
        if card.get("text"):
            els.append(panel("当时问的是什么 · 展开", explain))
        els.append(footer(card, mark))
    return card_json(head(title, COLOR.get(state, "grey")), els)


def button_values(content):
    """渲染结果里全部按钮的回调值（按出现顺序，含折叠面板里的）。"""
    out = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("tag") == "button":
                out.extend(b.get("value") for b in x.get("behaviors") or [] if b.get("type") == "callback")
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(content.get("body"))
    return out


# ---------- 引用回答 ----------
_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_REC = re.compile(r"^(就?按推荐(的)?(来|办|选)?|推荐(的|那个)?|就推荐的|同意推荐)$")
_PICK = re.compile(r"^(选|就选|要)?(第)?([0-9]{1,2}|[一二三四五六七八九十]|[①-⑩])(个|项|条|号)?$")


def parse_reply(text, card):
    """引用卡片打字回答 → 选项号（从 0 起）。只认一种明确的意思，含糊、超范围、卡片已结束都返回 None。"""
    if not is_open(card) or card.get("kind") != "ask":
        return None
    opts = options(card)
    t = re.sub(r"[\s，。,.!！~～「」“”\"']+", "", str(text or ""))
    if not t:
        return None
    if _REC.match(t):
        return recommend(card)
    exact = [i for i, o in enumerate(opts) if re.sub(r"\s+", "", o) == t]
    if len(exact) == 1:
        return exact[0]
    m = _PICK.match(t)
    if not m:
        return None
    g = m.group(3)
    n = NUMS.index(g) + 1 if g in NUMS else _CN.get(g) or int(g)          # ③ 的 isdigit() 也是真，先认圈号
    return n - 1 if 1 <= n <= len(opts) else None
