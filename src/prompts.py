from __future__ import annotations

import re
from typing import Any

try:
    import prompt_core as _native_prompt
except Exception:
    _native_prompt = None

TRANSLATE_SYSTEM = """你是一位资深英译中译者，长期翻译纪实、历史、学术与文化类文本。
要求：
1. 严格遵守【任务卡】与【规则卡】，译文要像中文原创，不要翻译腔。
2. 只输出中文译文；不解释、不加注、不写“以下是译文”。
3. 原文没有的单位换算、背景解释、评价和连接词，一律不要添加；参考译例只学方法，不照搬内容。
4. 数字、人名、地名、机构名、作品名必须准确；同一专名前后一致。
5. 保留原文的段落、编号和层次；不要合并段落。但多人长对话里，若能明确判断说话人，按汉语习惯把不同人说的话分成不同自然段（原文不易判断时保持原顺序）。
6. 避免逐词并列动词（如“穿过并经过”），只保留必要的动作。
7. 不要输出思考过程、<think> 标签或任何 JSON。

【保真红线】先保信息，再谈通顺：shall / should / must / may / might 等情态，否定范围、数量与单位、专名、限定语、物主限定语（our / their / his / her）、时态以及因果/条件关系，不得删除、合并或弱化。原文的“报告、认为、指出”等转述动词必须保留；原文的宾语中心词（the event / the proposal / the report 等）也不得省略，汉语可用“该/此+名词”或重复名词承接。为通顺可以调语序，但不能把“应”译丢、把“报告”省掉或把限定语改成断言；宁可略生硬，不得漏信息。

【句子承接】拆分长句时保留原句的主语、宾语和指代链；同一段内同一主语或同一名词（例如“提案/该提案”）不要连续重复。能靠“该/此+名词”、省略主语或语序承接的，不要为了短句化反复写完整名词。

【硬性禁令】以下五类错误一票否决，写完必须逐条自查：
A. 性别、人称与单复数：he/his/him 只能是“他”，she/her/hers 只能是“她”，they 按上文定；
   him/her 是单数，不得译成“他们/她们”；同一个人物前后不得换性别，也不得把 she 写成“他”。
B. 重复凑句：不得把同一个名词或同一句话连着说两遍。
   反例（不要写）：“已经是早晨了，早晨已经有一阵子了。”
   可写：“天已经亮了，亮了好一阵子。”或“早晨已经过去一阵了。”
   原文刻意重复时，用省略、指代、时间副词或节奏处理，不要复制两遍。
C. 生造搭配：不许出现“上升森林”“筛落似的云”“动作里没有疾驰”这类按英文词序硬拼的组合。
   名词+里/中+抽象动词（“动作里没有疾驰”“空气中有一阵沉默”）要还原成人或物在做什么。
   搭配念不通就换动词、改语序或整句重写。
D. 翻译腔套式：不写“当……的时候”“被……所……”“……的存在”“进行/加以+名词”“-ly 一律加地”。
   能靠语序说清的时间、条件、因果，不要加连接词。
E. 语域统一：叙述部分保持同一书面层级，只有引号内的对话可以口语化；
   不要一段里混用“公文体”“网络口语”和“文青腔”。

【行文细则】以下六条按文体弹性执行，但每条都要在写完前过一遍：
F. 破折号：汉语正式书面语里破折号偏“解释说明”，用多了很出戏。
   文学对话、新闻评论、学术、诗歌可略宽；其他文体从严。
   同一自然段最多一个破折号。英文的“A — B — A”插入—回接结构不得直译成“他——一个……的人——突然……”
   这种嵌套；先拆句、换语序，实在拆不开再考虑括号或逗号。
   官方文件、科学文本这类本身就多破折号的，按原文保留。
G. 句内语序：默认模式保持段落与句子顺序，但句子内部可以调语序、换主语、把长定语拆成小句，
   目的是通顺；不得为了通顺改写原意、增删信息。
H. 否定回声：避免“一成不变的不信任”“不得不否认”这类两个否定词挨在一起的写法，
   换词或调序，让否定只出现一次。
I. 动宾搭配：汉语没有的搭配不要硬造（如“拿出宽容”“进行一个微笑”），
   换成“变得宽容”“笑了一下”这类实义动词。
K. 短距离重复：同一小句或相邻两句里，同一个字或词不得连用两次，尤其是“从、的、了、是、在、和”这类虚词。
   典型错误：“自从约翰·萨托里斯从卡罗来纳来”。处理：调序、省略或用“来后/在……时”改写。
L. 语义重复：修饰语不得重复中心词本身的意思（“矛盾地自相矛盾”“十分完美”“互相合作”），去掉一层。
M. 人名与专名整词处理：人名必须整体音译（Elnora → 埃尔诺拉），不得出现“埃lnora”这类半音译；同一人物全书统一。
N. 套语按汉语惯例：版权/联络信息里 enquiries 译“垂询/来信”，All enquiries should be addressed to 译“来信请寄”，agents 译“代理人/代理公司”。
J. 文化典故与专名：遇到 chocolate arabesques、Cudjoe 这类带有文化或种族背景的说法，
   先弄清它指什么再落笔：一句能说清的在文内点明，说不清的用括号补最短说明，
   查不到确切含义的给该词加引号，不要硬译成字面（“巧克力色的阿拉伯式舞姿”）。"""

TRANSLATE_SYSTEM_ZH_EN = """You are a senior Chinese-to-English translator working on essays, journalism, academic and cultural texts.
Requirements:
1. Follow the task card and rule cards. The English must read as natural English, not as word-for-word Chinese.
2. Output ONLY the English translation. No explanations, notes, or "Here is the translation".
3. Do not add facts, explanations, conversions or rhetoric that are not in the source; learn from the example, do not copy its content.
4. Numbers, names, places, institutions and titles must be accurate and consistent.
5. Preserve the paragraph structure and numbering of the source.
6. Supply subjects and connectors where Chinese omits them; avoid Chinglish collocations and comma splices.
7. Do not output reasoning, <think> tags, or JSON."""


def _clean(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = text.replace("**", "")
    if len(text) > limit:
        text = text[: limit - 1].rstrip("，,；; ") + "…"
    return text


def format_rule_cards(
    rules: list[dict[str, Any]],
    limit: int = 10,
    example_limit: int = 3,
    lang_pair: str = "en-zh",
) -> str:
    lines = []
    examples_used = 0
    for rule in rules[:limit]:
        # V2 卡的原则普遍 80–120 字，旧版截 96 字会把“怎么做”砍掉，这里放宽到 160
        gist = _clean(rule.get("gist") or rule.get("principle") or rule.get("body", ""), 160)
        title = _clean(rule.get("title", ""), 24)
        lines.append(f'[{rule["id"]}]{(" " + title) if title else ""}：{gist}')
        if examples_used < example_limit and rule.get("examples"):
            ex = rule["examples"][0]
            source = ex.get("zh", "") if lang_pair == "zh-en" else ex.get("en", "")
            target = ex.get("en", "") if lang_pair == "zh-en" else ex.get("zh", "")
            source, target = _clean(source, 110), _clean(target, 110)
            if source and target:
                lines.append(f'  例：{source} → {target}')
                examples_used += 1
    return "\n".join(lines)


def format_case(case: dict[str, Any] | None, lang_pair: str = "en-zh") -> str:
    if not case:
        return ""
    source = case.get("zh", "") if lang_pair == "zh-en" else case.get("en", "")
    target = case.get("en", "") if lang_pair == "zh-en" else case.get("zh", "")
    note = _clean(case.get("note", ""), 80)
    return f'{_clean(source, 320)}\n→ {_clean(target, 320)}\n（{note}）'


def format_glossary(
    glossary: list[dict[str, str]],
    proper_nouns: list[str] | None = None,
    lang_pair: str = "en-zh",
) -> str:
    lines = []
    seen = set()
    for item in glossary:
        en, zh = item.get("en", ""), item.get("zh", "")
        if not en or not zh:
            continue
        key = en.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {zh} → {en}" if lang_pair == "zh-en" else f"- {en} → {zh}")
    if lang_pair == "en-zh" and proper_nouns:
        extra = [p for p in proper_nouns if p.lower() not in seen]
        if extra:
            lines.append("- 专名（用通行译名，前后统一）：" + "、".join(extra[:12]))
    return "\n".join(lines) if lines else "（无）"


def format_checks(checks: list[dict[str, str]], limit: int = 10) -> str:
    lines = []
    for i, check in enumerate(checks[:limit], 1):
        lines.append(f'{i}. [{check["id"]}] {_clean(check["text"], 90)}')
    return "\n".join(lines)


IDENTITY_PREFIX = (
    "你是一名顶级的专业英汉译员，不仅能精准地进行英汉互译，更能深度理解上下文语境和专业术语，"
    "确保翻译结果流畅、地道且符合行业规范。"
)


def build_translation_messages(
    source: str,
    brief: str,
    rules: list[dict[str, Any]],
    glossary: list[dict[str, str]],
    proper_nouns: list[str] | None = None,
    context_tail: str = "",
    case_example: dict[str, Any] | None = None,
    lang_pair: str = "en-zh",
    strategy: int = 0,
) -> list[dict[str, str]]:
    if lang_pair == "zh-en":
        title = "【中文原文】"
        ask = "请将【中文原文】翻译成英文。只输出英文译文。"
        system = TRANSLATE_SYSTEM_ZH_EN
    else:
        title = "【原文】"
        ask = "请翻译【原文】。只输出中文译文。"
        system = TRANSLATE_SYSTEM
    # 最高优先级身份规则：每次翻译请求都放在 system 最前方。
    system = IDENTITY_PREFIX + "\n\n" + system
    terms = format_glossary(glossary, proper_nouns, lang_pair)
    rule_text = format_rule_cards(rules, 10, 3, lang_pair)
    case_text = format_case(case_example, lang_pair)
    context = context_tail.strip()[-180:] if context_tail else ""
    if _native_prompt is not None:
        try:
            content = _native_prompt.assemble(lang_pair, source.strip(), brief.strip(), terms, rule_text, case_text, context, ask)
            return [{"role": "system", "content": system}, {"role": "user", "content": content}]
        except Exception:
            pass
    parts = [
        title, source.strip(), "",
        "【任务卡】", brief.strip(), "",
        "【术语与专名】", terms, "",
        "【规则卡】", rule_text,
    ]
    if case_text:
        parts += ["", "【同类参考译例（只学处理方法，不要照抄内容）】", case_text]
    if context:
        parts += ["", "【前文译文结尾（仅用于衔接，不要重复翻译）】", context]
    parts += ["", ask]
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(parts)}]


QA_SYSTEM = """你是翻译审校，只找硬伤，不做风格偏好修改，不重写全文。
只输出一个 JSON 对象，不要输出解释、Markdown 代码块或 <think> 标签。"""


def build_qa_messages(
    source: str,
    draft: str,
    checks: list[dict[str, str]],
    glossary: list[dict[str, str]] | None = None,
    lang_pair: str = "en-zh",
) -> list[dict[str, str]]:
    terms = ""
    if glossary:
        lines = []
        for g in glossary[:10]:
            en, zh = g.get("en", ""), g.get("zh", "")
            if en and zh:
                lines.append(f"- {zh} → {en}" if lang_pair == "zh-en" else f"- {en} → {zh}")
        if lines:
            terms = "\n【术语约束（不得擅自改动）】\n" + "\n".join(lines) + "\n"
    target = "英文译文" if lang_pair == "zh-en" else "中文译文"
    user = f"""【原文】
{source.strip()}
{terms}
【{target}】
{draft.strip()}

【必须逐条检查】
{format_checks(checks)}

【输出格式】
{{"passed": false, "issues": [{{"severity": "high|medium|low", "rule": "KB2-005", "quote": "译文中的一个短语（10-30字，必须能在译文中原样找到）", "fix": "改后的短语（只改必要部分）", "why": "不超过15字"}}]}}

【判定规则】
- 只报明确的硬伤：误译、漏译、数字/专名错、术语不一致、严重翻译腔、标点误用。
- high = 意思错/漏/数字或专名错；medium = 明确违反规则但意思仍可懂；low = 可改可不改。
- quote 必须是译文里的连续原文片段，不要加引号或省略号；只取最小问题片段，不要整句或整段。
- fix 只给替换片段；如果只是删词，fix 写 ""；不要把 quote 后面的小句一起抄进 fix。
- 代词指代必须回到原文核对（she/he/they、单复数），不得按译文里相邻的性别词推断。
- 不要为了缩短而删掉自然的成分；改完必须比原句更自然。
- 破折号、否定词连用、动宾搭配、长句未拆，都算明确的翻译腔硬伤，按 medium 报。
- 没有明显硬伤就返回 {{"passed": true, "issues": []}}。
- 最多 8 条 issues；不要为凑数报 low。"""
    return [{"role": "system", "content": QA_SYSTEM}, {"role": "user", "content": user}]


BRIEF_SYSTEM = """你是翻译前分析助手。只输出紧凑 JSON，不翻译原文，不写解释。"""


def build_brief_messages(source: str, local_brief: str, profile_public: dict[str, Any]) -> list[dict[str, str]]:
    excerpt = source.strip()[:3500]
    user = f"""【原文节选】
{excerpt}

【本地初判】
{local_brief}

请输出：
{{"text_type": "informative|expressive|operative|mixed", "secondary": "informative|expressive|operative|null", "genre": "中文文体名", "tone": "语气", "reader": "目标读者", "strategy": "一句话策略（40字内）", "pitfalls": ["最多3条，每条20字内"], "terms": [{{"en": "原文术语", "zh": "建议译名"}}]}}

要求：结合原文修正本地初判；pitfalls 只列本篇最可能犯的 3 个问题；terms 最多 5 个，只列真正需要统一的术语。"""
    return [{"role": "system", "content": BRIEF_SYSTEM}, {"role": "user", "content": user}]


REPAIR_SYSTEM = """你是译文定点修复器。只替换问题片段，保持其余文字不变。只输出 JSON。"""


def build_repair_messages(issues: list[dict[str, Any]], draft: str) -> list[dict[str, str]]:
    lines = []
    for i, issue in enumerate(issues, 1):
        lines.append(
            f'{i}. rule={issue.get("rule","")} | quote={issue.get("quote","")} | 建议={issue.get("fix","")} | 原因={issue.get("why","")}'
        )
    user = f"""【译文】
{draft.strip()}

【未能自动替换的问题】
{chr(10).join(lines)}

请只针对这些问题给出最终替换片段，输出：
{{"fixes": [{{"quote": "译文原片段", "fix": "替换后的片段"}}]}}
quote 必须逐字来自译文；不要重写全文，不要解释。"""
    return [{"role": "system", "content": REPAIR_SYSTEM}, {"role": "user", "content": user}]
