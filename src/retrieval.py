from __future__ import annotations

import math
import re

from fast_match import build as ac_build
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

CJK = r"\u4e00-\u9fff"

# 高频但不需要当作“专名”的词
STOP_CAPS = {
    "The", "A", "An", "This", "That", "These", "Those", "It", "He", "She", "They", "We", "I",
    "But", "And", "Or", "If", "When", "While", "After", "Before", "During", "In", "On", "At",
    "For", "From", "To", "Of", "As", "By", "With", "His", "Her", "Their", "Our", "My", "Its",
    "There", "Here", "Then", "Now", "So", "Yet", "Not", "No", "Yes", "One", "Two", "Three",
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday", "English", "Chinese", "China",
}

LY_KEEP = {"only", "early", "family", "reply", "apply", "supply", "likely", "daily", "weekly",
           "monthly", "yearly", "friendly", "lonely", "lovely", "silly", "ugly", "holy", "italy",
           "assembly", "monopoly", "multiply", "imply", "rely", "july"}

CORE_RULES = ["KB2-001", "KB2-002", "KB4-018"]

GLOSSARY_STOP = {
    "who", "may", "march", "one", "two", "three", "four", "five", "us", "it", "in", "on", "at",
    "by", "for", "from", "to", "of", "and", "or", "the", "a", "an", "is", "are", "was", "were",
    "be", "been", "being", "has", "have", "had", "do", "does", "did", "can", "could", "will",
    "would", "shall", "should", "not", "no", "yes", "i", "you", "he", "she", "we", "they",
    "this", "that", "these", "those", "there", "their", "his", "her", "its", "our", "your", "my",
    "adding", "omission", "splitting", "equivalence", "fidelity", "smoothness",
}
TYPE_RULES = {
    "informative": "KB3-002",
    "expressive": "KB3-003",
    "operative": "KB3-004",
    "mixed": "KB3-005",
}
FEATURE_RULES: list[tuple[str, list[str]]] = [
    ("passive", ["KB2-005", "KB4-007"]),
    ("long_sentence", ["KB2-003", "KB2-014"]),
    ("connectives", ["KB2-011", "KB2-012"]),
    ("dash", ["KB4-006"]),
    ("proper_noun", ["KB5-001"]),
    ("nominalization", ["KB2-006", "KB4-008"]),
    ("ly_adverb", ["KB2-007"]),
    ("idiom", ["KB5-008", "KB4-002"]),
    ("negation", ["KB2-009"]),
    ("pronoun", ["KB2-010"]),
    ("preposition", ["KB2-008"]),
    ("cultural", ["KB4-003"]),
]

FALLBACK_RULES = ["KB2-004", "KB4-004", "KB1-006", "KB3-005", "KB2-013", "KB4-005"]

PITFALL_RULES = {
    "passive": "少用“被”，改主动或意念被动",
    "long_sentence": "长句拆分，别堆前置定语",
    "connectives": "少用“当/如果/和”的公式化译法",
    "ly_adverb": "不要见 -ly 就加“地”",
    "nominalization": "抽象名词转动词",
    "dash": "破折号只用于强调，列举改冒号",
    "proper_noun": "专名统一、用通行译名",
    "idiom": "习语意译，不硬译",
    "negation": "注意否定转移",
    "pronoun": "代词该省则省",
    "preposition": "介词常转动词",
    "cultural": "文化专有项做归化或解释",
}


@dataclass
class TextProfile:
    text: str
    word_count: int
    char_count: int
    sentence_count: int
    avg_sentence_len: float
    max_sentence_len: int
    text_type: str
    secondary_type: str | None
    genre: str
    tone: str
    register: str
    domain: str
    features: dict[str, Any] = field(default_factory=dict)
    proper_nouns: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    type_source: str = "local"
    lang_pair: str = "en-zh"

    def brief(self, reader: str = "general", strategy: int = 0, lang_pair: str | None = None) -> str:
        lang_pair = lang_pair or self.lang_pair
        reader_map = {
            "general": "普通中文读者" if lang_pair == "en-zh" else "英语普通读者",
            "professional": "中文专业读者" if lang_pair == "en-zh" else "英语专业读者",
            "student": "中文学生读者" if lang_pair == "en-zh" else "英语学生读者",
            "child": "中文儿童读者" if lang_pair == "en-zh" else "英语儿童读者",
        }
        type_map = {
            "informative": "信息型",
            "expressive": "表情型",
            "operative": "感染型",
            "mixed": "混合型",
        }
        main = type_map.get(self.text_type, self.text_type)
        secondary = f"（次：{type_map.get(self.secondary_type, self.secondary_type)}）" if self.secondary_type else ""
        if lang_pair == "zh-en":
            pitfalls = []
            for key, msg in {
                "zero_subject": "补出主语", "topic_comment": "话题转主谓",
                "verb_stacking": "确定主动词", "four_char": "四字格译意义",
                "punctuation": "避免逗号串句", "connectives": "补出逻辑连接",
                "modifier": "长定语后置", "passive": "被字句按焦点选语态",
            }.items():
                if self.features.get(key):
                    pitfalls.append(msg)
            if not pitfalls:
                pitfalls = ["补主语", "补连接", "避免中式英语"]
        else:
            pitfalls = [PITFALL_RULES[k] for k in PITFALL_RULES if self.features.get(k)]
        strategy_text = self._strategy(strategy, lang_pair)
        lines = [
            f"文本类型：{main}{secondary}；文体：{self.genre}；读者：{reader_map.get(reader, reader)}；语气：{self.tone}。",
            f"策略：{strategy_text}",
            f"注意：{'；'.join(pitfalls[:4]) if pitfalls else '准确、通顺、少翻译腔'}。",
        ]
        return "\n".join(lines)

    def _strategy(self, bias: int = 0, lang_pair: str = "en-zh") -> str:
        if lang_pair == "zh-en":
            if self.text_type == "informative":
                base = "先保证事实、数字、逻辑与术语准确；补出汉语省略的主语和连接词，避免中式英语。"
            elif self.text_type == "expressive":
                base = "保留原文的意象、节奏与语气；可用英语修辞，但不逐字硬译四字格和典故。"
            elif self.text_type == "operative":
                base = "以英语读者的反应为先，可改写、编译或创译，保留号召力。"
            else:
                base = "先按主导功能处理，再照顾次要功能；英文必须自然，不能是中文语序的逐字替换。"
        else:
            if self.text_type == "informative":
                base = "以语意翻译为主，先保证事实、数字、逻辑准确，可显化连接，不添修辞。"
            elif self.text_type == "expressive":
                base = "以传意翻译为主，保留叙事节奏、语气与修辞，可用四字格，但不堆砌。"
            elif self.text_type == "operative":
                base = "以功能对等为主，可意译、编译，优先保留号召力与读者反应。"
            else:
                base = "先按主导功能处理，再照顾次要功能；以语意翻译为底线，关键处再创造。"
        if bias < 0:
            base += " 整体偏直译：尽量保留原文语序、意象和句式，但不能牺牲目标语可读性。"
        elif bias > 0:
            base += " 整体偏意译：优先目标语自然表达，可重组句法、意译习语和文化意象。"
        else:
            base += " 整体取中：直译与意译按语境调整。"
        return base

    def public(self) -> dict[str, Any]:
        return {
            "word_count": self.word_count,
            "char_count": self.char_count,
            "sentence_count": self.sentence_count,
            "avg_sentence_len": round(self.avg_sentence_len, 1),
            "max_sentence_len": self.max_sentence_len,
            "text_type": self.text_type,
            "secondary_type": self.secondary_type,
            "genre": self.genre,
            "tone": self.tone,
            "register": self.register,
            "domain": self.domain,
            "features": {k: v for k, v in self.features.items() if v},
            "proper_nouns": self.proper_nouns,
            "numbers": self.numbers,
            "type_source": self.type_source,
            "lang_pair": self.lang_pair,
        }


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“'‘])", text)
    return [p.strip() for p in parts if p.strip()]


def words_of(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", text)


def detect_proper_nouns(text: str) -> list[str]:
    sentences = split_sentences(text)
    first_words = set()
    for s in sentences:
        m = re.match(r"^[\"“'‘]?([A-Z][a-z]+)", s)
        if m:
            first_words.add(m.group(1))
    candidates: Counter[str] = Counter()
    pattern = r"\b(?:The\s+|A\s+|An\s+)?([A-Z][a-z]+(?:\s+(?:of\s+|the\s+|de\s+|van\s+|von\s+)?[A-Z][a-z]+){0,4})\b"
    for m in re.finditer(pattern, text):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        if not name or name in STOP_CAPS:
            continue
        if name.split()[0] in STOP_CAPS and len(name.split()) == 1:
            continue
        if name.split()[0] in first_words and len(name.split()) == 1:
            continue
        if len(name) < 3:
            continue
        candidates[name] += 1
    # 只保留更像专名的：出现 >=1 且不是句首普通词；按长度和频次排序
    names = sorted(candidates, key=lambda x: (-len(x.split()), -candidates[x], x))[:15]
    return names


def _detect_profile_en(text: str) -> TextProfile:
    text = text.strip()
    ws = words_of(text)
    sentences = split_sentences(text)
    n_words = len(ws)
    n_sent = max(1, len(sentences))
    lens = [len(words_of(s)) for s in sentences] or [0]
    avg_len = sum(lens) / max(1, len(lens))
    max_len = max(lens) if lens else 0

    low = text.lower()
    passive = len(re.findall(r"\b(?:is|are|was|were|be|been|being|get|gets|got|gotten)\s+(?:\w+ly\s+)?[\w-]+(?:ed|en)\b", low))
    rel = len(re.findall(r"\b(?:which|who|whom|whose)\b", low)) + len(re.findall(r"\bthat\b", low))
    connectors = len(re.findall(r"\b(?:when|if|and|but|because|although|though|while|since|as|unless|until)\b", low))
    ly = [w for w in re.findall(r"\b([a-z]{4,}ly)\b", low) if w not in LY_KEEP]
    nominal = len(re.findall(r"\b[a-z]{4,}(?:tion|sion|ment|ness|ity|ance|ence|ship|ure|ism)\b", low))
    dashes = len(re.findall(r"--|—|–", text))
    semis = text.count(";") + text.count("；")
    parens = text.count("(") + text.count("（")
    quotes = len(re.findall(r"[\"“”'‘’]", text))
    first_person = len(re.findall(r"\b(?:i|we|me|us|my|our|mine|ours)\b", low))
    second_person = len(re.findall(r"\b(?:you|your|yours|yourself)\b", low))
    past = len(re.findall(r"\b(?:was|were|had|did|said|went|came|saw|found|made|took|looked|seemed)\b", low))
    numbers = re.findall(
        r"\b\d[\d,]*(?:\.\d+)?\s*(?:miles?|kilometers?|kilometres?|feet|foot|meters?|metres?|yards?|years?|months?|days?|percent|per cent|°|degrees?)?\b",
        text,
    )
    numbers = [n.strip() for n in numbers if n.strip()]
    proper = detect_proper_nouns(text)
    long_sentences = sum(1 for n in lens if n >= 32)

    # 文本类型打分
    info = nominal * 1.0 + passive * 1.1 + connectors * 0.25 + len(numbers) * 0.9
    expr = first_person * 1.2 + past * 0.45 + quotes * 0.35 + min(nominal, 6) * 0.2
    oper = second_person * 1.6 + len(re.findall(r"[!！]", text)) * 2.0
    per_sent = max(1, n_sent)
    info_r, expr_r, oper_r = info / per_sent, expr / per_sent, oper / per_sent

    text_type = "informative"
    secondary: str | None = None
    if oper_r > max(info_r, expr_r) * 1.15 and second_person >= 3:
        text_type = "operative"
        secondary = "informative" if info_r > 1 else None
    elif past >= 6 and first_person >= 2 and expr_r >= info_r * 0.8:
        text_type = "expressive"
        secondary = "informative" if info_r > expr_r * 0.7 else None
    elif past >= 8 and long_sentences >= 3 and len(numbers) >= 2 and info_r >= 1.0:
        # 历史/纪实类：以信息传递为主，同时保留叙事与描写
        text_type = "informative" if info_r >= expr_r * 0.75 else "expressive"
        secondary = "expressive" if text_type == "informative" else "informative"
    elif info_r >= 1.1 or (nominal >= 4 and passive >= 2):
        text_type = "informative"
    elif expr_r >= 1.4:
        text_type = "expressive"
    else:
        text_type = "mixed"
        secondary = "informative" if info_r >= expr_r else "expressive"

    # 文体
    if re.search(r"\b(?:i|we)\b.{0,40}\b(?:was|were|had|lived|remember)\b", low) and text_type == "expressive":
        genre = "叙事散文"
    elif second_person >= 3 and re.search(r"\b(?:you|your)\b", low) and re.search(r"[!！]", text):
        genre = "广告 / 宣传文案"
    elif re.search(r"\b(?:study|research|data|survey|experiment|hypothesis|methodology)\b", low):
        genre = "学术 / 说明文"
    elif re.search(r"\b(?:according to|reported|journalist|correspondent|news)\b", low):
        genre = "新闻报道"
    elif re.search(r"\b(?:court|statute|clause|plaintiff|defendant|contract)\b", low):
        genre = "法律文本"
    elif re.search(r"\b(?:install|operation|manual|specification|device)\b", low):
        genre = "技术文档"
    elif re.search(r"\b(?:ladies and gentlemen|speech|address)\b", low):
        genre = "演讲"
    elif proper and past >= 5:
        genre = "历史纪实 / 叙事散文"
    else:
        genre = "一般非虚构"

    # 语气
    formality = nominal + passive
    if re.search(r"\b(?:don't|can't|won't|isn't|it's|i'm)\b", low):
        tone = "自然、略带口语"
    elif formality >= 8:
        tone = "正式、客观"
    elif quotes >= 2:
        tone = "叙事、带描写"
    else:
        tone = "平实、偏书面"

    # 领域
    domain_scores = {
        "政治法律": len(re.findall(r"\b(?:government|law|laws|court|party|state|president|policy|rights|constitution|contract|contracts|arbitration|shall|jurisdiction|clause)\b", low)),
        "科技医学": len(re.findall(r"\b(?:technology|data|system|software|medical|health|disease|research|energy|patient|patients|drug|drugs|clinical|adverse|diagnosis|treatment)\b", low)),
        "经济商业": len(re.findall(r"\b(?:market|company|companies|business|trade|bank|economy|price|investment|financial|finance|revenue|board|corporate|counterparty|stakeholder|stakeholders)\b", low)),
        "文学历史": len(re.findall(r"\b(?:century|village|war|history|author|book|story|people|province|novel|protagonist|narrator|poem|poetry)\b", low)),
        "教育": len(re.findall(r"\b(?:school|student|teacher|education|learning|class|academic|analysis|study|studies)\b", low)),
        "旅游": len(re.findall(r"\b(?:tourist|tourists|visitor|visitors|museum|ticket|tickets|hotel|travel|tourism|attraction)\b", low)),
    }
    domain = max(domain_scores, key=domain_scores.get) if max(domain_scores.values()) > 0 else "通用"

    features = {
        "passive": passive,
        "long_sentence": long_sentences,
        "connectives": connectors,
        "ly_adverb": len(ly),
        "nominalization": nominal,
        "dash": dashes,
        "semicolon": semis,
        "parenthesis": parens,
        "quote": quotes,
        "first_person": first_person,
        "second_person": second_person,
        "past": past,
        "relative_clause": rel,
        "proper_noun": len(proper),
        "number": len(numbers),
        "idiom": 0,
        "negation": len(re.findall(r"\b(?:not|no|never|nothing|nobody|neither|nor|hardly|scarcely)\b", low)),
        "pronoun": len(re.findall(r"\b(?:he|she|it|they|him|her|them|his|its|their)\b", low)),
        "preposition": len(re.findall(r"\b(?:of|to|in|on|at|by|with|from|for|about|through|into|over|under)\b", low)),
    }
    return TextProfile(
        text=text,
        word_count=n_words,
        char_count=len(text),
        sentence_count=len(sentences),
        avg_sentence_len=avg_len,
        max_sentence_len=max_len,
        text_type=text_type,
        secondary_type=secondary,
        genre=genre,
        tone=tone,
        register="书面语" if formality >= 5 else "通用语",
        domain=domain,
        features=features,
        proper_nouns=proper,
        numbers=numbers[:20],
    )


def split_sentences_zh(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def detect_profile_zh(text: str) -> TextProfile:
    text = text.strip()
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    sentences = split_sentences_zh(text)
    lens = [len(re.findall(r"[\u4e00-\u9fff]", s)) for s in sentences] or [0]
    n = max(1, len(cjk_chars))
    n_sent = max(1, len(sentences))
    avg_len = sum(lens) / max(1, len(lens))

    passive = len(re.findall(r"被|遭到|受到|让|给", text))
    ba = len(re.findall(r"把|将", text))
    zero_subject = len(re.findall(r"(?:^|[。！？；\n])\s*(?:[是为有要不没请让应该必须可以]|必须|应该|可以)", text))
    topic_comment = len(re.findall(r"^[^，。！？]{2,12}[，,]\s*(?:我|我们|他|她|它|这|那|大家)", text, re.M))
    verb_stacking = len(re.findall(r"[，,]\s*(?:他|她|我|你)?[^，。！？]{0,6}(?:走|跑|拿|说|想|要|做|看|听|读|写|吃|穿|用|坐|站|笑|哭)", text))
    four_char = len(re.findall(r"(?<![\u4e00-\u9fff])[\u4e00-\u9fff]{4}(?![\u4e00-\u9fff])", text))
    modifier = len(re.findall(r"的", text))
    connectives = len(re.findall(r"因为|所以|但是|然而|如果|虽然|并且|而且|因此|于是|同时|尽管", text))
    particles = len(re.findall(r"[吧呢啊嘛呀哦啦]", text))
    punctuation = len(re.findall(r"[，、；：]", text))
    numbers = re.findall(r"\d+(?:\.\d+)?(?:%|％|万|亿|千|百|十|里|亩|斤|公里|米|岁|年|月|日)?", text)
    names = re.findall(r"《[^》]{1,30}》", text)
    names += re.findall(r"[\u4e00-\u9fff]{2,10}(?:省|市|县|村|山|河|湖|大学|公司|协会|政府|法院)", text)
    proper = []
    for name in names:
        if name not in proper:
            proper.append(name)
    cultural = len(re.findall(r"春节|中秋|端午|清明|七夕|成语|典故|四大|文房四宝|科举|京剧|书法", text))

    info = len(re.findall(r"研究|数据|报告|调查|分析|因此|根据|显示|表明|统计|实验", text)) * 1.6 + connectives * 0.4
    expr = len(re.findall(r"我|我们|记忆|故乡|母亲|父亲|童年|岁月|月光|风|雨|梦", text)) * 0.8 + len(re.findall(r"[“”]", text)) * 0.8 + particles * 0.5
    oper = len(re.findall(r"请|禁止|欢迎|让我们|必须|应该|行动起来|加入", text)) * 1.6
    info_r, expr_r, oper_r = info / n_sent, expr / n_sent, oper / n_sent

    text_type = "informative"
    secondary = None
    if oper_r > max(info_r, expr_r) * 1.15:
        text_type, secondary = "operative", "informative"
    elif expr_r >= info_r and re.search(r"我|我们|记忆|故乡|月光|风|雨|梦", text):
        text_type, secondary = "expressive", "informative"
    elif info_r > 0 and info_r >= expr_r:
        text_type, secondary = "informative", ("expressive" if four_char >= 2 else None)
    else:
        text_type, secondary = "mixed", "informative"

    if re.search(r"散文|随笔|记忆|故乡|童年", text):
        genre = "散文 / 叙事"
    elif re.search(r"新闻|记者|报道|据悉", text):
        genre = "新闻报道"
    elif re.search(r"研究|论文|数据|报告|分析", text):
        genre = "学术 / 说明文"
    elif re.search(r"公告|条例|办法|合同|法律", text):
        genre = "公文 / 法律"
    elif re.search(r"欢迎|景区|游客|旅游", text):
        genre = "旅游介绍"
    elif re.search(r"尊敬的|女士们|先生们|演讲", text):
        genre = "演讲"
    else:
        genre = "一般非虚构"

    domain_scores = {
        "政治法律": len(re.findall(r"政府|法律|政策|党|国家|法院|条例|权利", text)),
        "经济商业": len(re.findall(r"市场|公司|经济|贸易|银行|投资|产业", text)),
        "文学历史": len(re.findall(r"文学|历史|诗|小说|散文|故乡|岁月|村庄", text)),
        "科技医学": len(re.findall(r"技术|数据|系统|软件|医学|健康|研究|能源", text)),
        "教育": len(re.findall(r"学校|学生|教师|教育|课程|大学", text)),
    }
    domain = max(domain_scores, key=domain_scores.get) if max(domain_scores.values()) > 0 else "通用"
    tone = "书面、正式" if len(re.findall(r"应当|必须|根据|因此|鉴于", text)) >= 2 else "自然、书面"

    features = {
        "passive": passive, "ba": ba, "zero_subject": zero_subject,
        "topic_comment": topic_comment, "verb_stacking": verb_stacking,
        "four_char": four_char, "modifier": modifier, "connectives": connectives,
        "particle": particles, "punctuation": punctuation, "number": len(numbers),
        "proper_noun": len(proper), "cultural": cultural, "long_sentence": sum(1 for x in lens if x >= 40),
        "repetition": len(re.findall(r"([\u4e00-\u9fff]{2,6})[^。！？]{0,20}\1", text)),
        "idiom": four_char, "text_type": 1,
    }
    return TextProfile(
        text=text, word_count=len(cjk_chars), char_count=len(text),
        sentence_count=len(sentences), avg_sentence_len=avg_len, max_sentence_len=max(lens),
        text_type=text_type, secondary_type=secondary, genre=genre, tone=tone,
        register="书面语", domain=domain, features=features, proper_nouns=proper[:15],
        numbers=numbers[:20], type_source="local", lang_pair="zh-en",
    )


def detect_profile(text: str, lang: str = "en") -> TextProfile:
    if lang == "zh":
        return detect_profile_zh(text)
    return _detect_profile_en(text)


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = re.findall(r"[a-z][a-z'-]+", text)
    for run in re.findall(rf"[{CJK}]+", text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1 = k1
        self.b = b
        self.n = len(docs)
        self.avg = sum(len(d) for d in docs) / max(1, self.n)
        self.df: Counter[str] = Counter()
        for doc in docs:
            self.df.update(set(doc))
        self.tf = [Counter(doc) for doc in docs]

    def score(self, query: list[str], idx: int) -> float:
        tf = self.tf[idx]
        dl = len(self.docs[idx])
        score = 0.0
        for q in query:
            if q not in tf:
                continue
            df = self.df.get(q, 0)
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            freq = tf[q]
            score += idf * (freq * (self.k1 + 1)) / (freq + self.k1 * (1 - self.b + self.b * dl / max(1, self.avg)))
        return score


class KnowledgeBase:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.schema = payload.get("schema", "")
        self.meta = payload.get("meta", {})
        self.revision = int(payload.get("revision", 1))
        self.updated_at = payload.get("updated_at", "")
        self.rules = payload.get("rules", [])
        self.cards = payload.get("cards") or self.rules
        self.masters = payload.get("masters") or [r for r in self.cards if r.get("card_type") == "master"]
        self.cases = payload.get("cases", [])
        self.glossary = payload.get("glossary", [])
        self.by_id = {r["id"]: r for r in self.rules}
        docs = []
        for r in self.rules:
            principle = r.get("principle") or r.get("gist") or r.get("body", "")
            examples = " ".join(e.get("en", "") for e in r.get("examples", []))
            trigger_text = " ".join(
                str(t.get("key", "")) if isinstance(t, dict) else str(t)
                for t in (r.get("triggers") or [])
            )
            docs.append(tokenize(f'{r.get("title", "")} {trigger_text} {principle} {examples}'))
        self.bm25 = BM25(docs)
        self._bm25_sig = (id(self.rules), len(self.rules))
        self._ac_cache: dict[str, tuple[int, Any]] = {}
        self._case_index: dict[str, list[dict[str, Any]]] | None = None

    # ---------- 索引 ----------

    def _ensure_bm25(self) -> None:
        sig = (id(self.rules), len(self.rules))
        if getattr(self, "_bm25_sig", None) == sig:
            return
        docs = []
        for r in self.rules:
            principle = r.get("principle") or r.get("gist") or r.get("body", "")
            examples = " ".join(e.get("en", "") for e in r.get("examples", []))
            trigger_text = " ".join(
                str(t.get("key", "")) if isinstance(t, dict) else str(t)
                for t in (r.get("triggers") or [])
            )
            docs.append(tokenize(f'{r.get("title", "")} {trigger_text} {principle} {examples}'))
        self.bm25 = BM25(docs)
        self._bm25_sig = sig

    def _terms_ac(self, lang_pair: str) -> tuple[Any, list[dict[str, Any]]]:
        """按语言方向构建术语自动机（懒加载，术语表变化时重建）。"""
        entries = [
            g for g in self.glossary
            if g.get("enabled") is not False
            and (g.get("lang_pair", "en-zh") == lang_pair or lang_pair == "zh-en")
        ]
        sig = (len(entries), id(self.glossary))
        cached = self._ac_cache.get(lang_pair)
        if cached and cached[0] == sig:
            return cached[1], entries
        key = "zh" if lang_pair == "zh-en" else "en"
        ac = ac_build([str(g.get(key, "")) for g in entries])
        self._ac_cache[lang_pair] = (sig, ac)
        return ac, entries

    def get(self, rid: str) -> dict[str, Any] | None:
        return self.by_id.get(rid)

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = tokenize(query)
        scored = [(self.bm25.score(q, i), r) for i, r in enumerate(self.rules)]
        scored = [(s, r) for s, r in scored if s > 0 and r.get("enabled", True)]
        scored.sort(key=lambda x: -x[0])
        return [{**r, "score": round(s, 2)} for s, r in scored[:limit]]

    def _eligible(self, rule: dict[str, Any], lang_pair: str, poetry: bool = False, domain: str = "default") -> bool:
        rule_domain = str(rule.get("domain") or "general")
        # 领域口径统一：默认档只启用“通用”规则；选了某个领域则启用“通用 + 该领域”。
        # 不再用 kb 编号（>=7）做隐式开关——KB7-15 里同样有通用规则，之前会被整段关掉，
        # 导致“买了 260 条却只生效 182 条”，和界面上看到的条数对不上。
        if domain in {"default", "general", ""}:
            if rule_domain not in {"general", ""}:
                return False
        elif rule_domain not in {"general", "", domain}:
            return False
        if rule.get("poetry") and not poetry:
            return False
        if not rule.get("enabled", True):
            return False
        direction = str(rule.get("direction") or rule.get("lang_pair") or "both")
        if direction not in {"both", lang_pair}:
            return False
        if lang_pair == "en-zh":
            # 术语清单交给 glossary 处理；标题/背景规则不占固定卡位
            if rule.get("kb") == 5 and rule["id"] not in {"KB5-001", "KB5-002"}:
                return False
            if rule["id"] in {"KB3-018", "KB1-014"}:
                return False
        return True

    def _select_v2(
        self,
        profile: TextProfile,
        limit: int,
        lang_pair: str,
        poetry: bool,
        domain: str,
        text: str = "",
    ) -> list[dict[str, Any]]:
        self._ensure_bm25()
        from v2_selector import select as select_v2

        active = {k for k, v in profile.features.items() if v}
        active.add(profile.text_type)
        if profile.secondary_type:
            active.add(profile.secondary_type)
        if profile.proper_nouns:
            active.add("proper_noun")
        domain_code = {
            "政治法律": "legal", "科技医学": "tech_medical", "经济商业": "business",
            "文学历史": "literary", "教育": "academic", "旅游": "tourism",
        }.get(profile.domain)
        if domain_code:
            active.add(domain_code)
        effective_domain = "poetry" if poetry else ("general" if domain in {"default", "general"} else domain)
        if effective_domain == "general" and domain_code:
            effective_domain = domain_code
        # 用原文算一遍 BM25，把“和这篇文本真的有关”的卡排前面
        relevance: dict[str, float] = {}
        sample = (text or profile.text or "")[:6000]
        if sample:
            q = tokenize(sample)
            for idx, rule in enumerate(self.rules):
                score = self.bm25.score(q, idx)
                if score > 0:
                    relevance[str(rule.get("id"))] = score
        result = select_v2(
            self.cards,
            direction=lang_pair,
            domain=effective_domain,
            stage="translate",
            active_triggers=active,
            kb_topk={str(k): int(v) for k, v in (self.meta.get("kb_topk") or {}).items()},
            parent_topk=int(self.meta.get("parent_topk", 4)),
            limit=limit,
            relevance=relevance,
            text_type=profile.text_type,
            text=sample,
        )
        selected = list(result.get("selected", []))
        # 只有分支卡不够填满卡位时，才补一张所属总卡作默认动作；
        # 不再用 G-FACT / G-TERM 这类泛化总卡占掉一整个卡位。
        context: list[dict[str, Any]] = []
        if selected and len(selected) < limit:
            parent_id = str(selected[0].get("parent_id") or "")
            by_id = {c.get("id"): c for c in self.masters}
            if parent_id in by_id:
                context = [by_id[parent_id]]
        out = []
        for card in context + selected:
            item = dict(card)
            item.setdefault("gist", item.get("principle", ""))
            item.setdefault("why", "总卡" if item.get("card_type") == "master" else "分支卡")
            item.setdefault("examples", [])
            out.append(item)
        self.last_v2_conflicts = result.get("conflicts", [])
        return out[: max(4, limit)]

    def select(
        self,
        profile: TextProfile,
        limit: int = 10,
        lang_pair: str | None = None,
        strategy: int = 0,
        poetry: bool = False,
        domain: str = "default",
        text: str = "",
    ) -> list[dict[str, Any]]:
        lang_pair = lang_pair or profile.lang_pair
        if self.schema == "translation-rule-card-v2":
            return self._select_v2(profile, limit, lang_pair, poetry, domain, text)
        limit = max(4, limit)
        selected: list[dict[str, Any]] = []
        reasons: dict[str, str] = {}
        per_kb: dict[int, int] = {}
        core = ["KB6-001", "KB6-002", "KB6-003"] if lang_pair == "zh-en" else CORE_RULES

        def add(rule: dict[str, Any], why: str, force: bool = False) -> None:
            if len(selected) >= limit or rule["id"] in reasons or not self._eligible(rule, lang_pair, poetry, domain):
                return
            if not force and per_kb.get(rule["kb"], 0) >= (10 if rule["kb"] == 6 else 7):
                return
            selected.append(rule)
            reasons[rule["id"]] = why
            per_kb[rule["kb"]] = per_kb.get(rule["kb"], 0) + 1

        for rid in core:
            if rid in self.by_id:
                add(self.by_id[rid], "核心规则", force=True)
        if lang_pair == "zh-en":
            type_rid = "KB6-020"
        else:
            type_map = {
                "informative": "KB3-010", "expressive": "KB3-011",
                "operative": "KB3-012", "mixed": "KB3-013",
            }
            type_rid = type_map.get(profile.text_type)
        if type_rid and type_rid in self.by_id:
            add(self.by_id[type_rid], f"文本类型：{profile.text_type}", force=True)

        active = {k for k, v in profile.features.items() if v}
        q = tokenize(profile.text[:5000])
        scored: list[tuple[float, dict[str, Any], str]] = []
        literal_triggers = {"literal", "fidelity", "proper_noun", "number", "structure", "long_sentence", "modifier", "particle"}
        free_triggers = {"free", "style", "rhythm", "idiom", "four_char", "cultural", "domestication", "paraphrase", "explicitation", "operative"}
        for i, rule in enumerate(self.rules):
            if rule["id"] in reasons or not self._eligible(rule, lang_pair):
                continue
            score = float(rule.get("priority", 2)) * 1.8
            why = []
            triggers = {
                str(t.get("key")) if isinstance(t, dict) and t.get("key") else str(t)
                for t in (rule.get("triggers") or []) if t is not None
            }
            hit = triggers & active
            if hit:
                score += 2.6 * min(3, len(hit))
                why.append("特征：" + "/".join(sorted(hit)))
            text_types = set(rule.get("text_types", []))
            if profile.text_type in text_types:
                score += 4.0
                why.append(f"文体：{profile.text_type}")
            elif profile.secondary_type and profile.secondary_type in text_types:
                score += 1.8
                why.append(f"次要文体：{profile.secondary_type}")
            if rule["kb"] == 5 and profile.proper_nouns:
                score += 1.5
            bm = self.bm25.score(q, i)
            if bm:
                score += min(3.0, bm * 0.35)
                why.append("关键词相关")
            if rule["id"] in core:
                score += 6
            literal_ids = {"KB6-002", "KB6-005", "KB6-011", "KB6-012", "KB6-013", "KB6-017"}
            free_ids = {"KB6-004", "KB6-006", "KB6-010", "KB6-016", "KB6-018", "KB6-020"}
            if strategy < 0:
                if triggers & literal_triggers or rule["id"] in literal_ids:
                    score += 3.0
                    why.append("直译倾向")
                if triggers & free_triggers or rule["id"] in free_ids:
                    score -= 2.0
            elif strategy > 0:
                if triggers & free_triggers or rule["id"] in free_ids:
                    score += 3.0
                    why.append("意译倾向")
                if triggers & literal_triggers or rule["id"] in literal_ids:
                    score -= 2.0
            scored.append((score, rule, "；".join(why) or "优先级补充"))
        scored.sort(key=lambda x: (-x[0], x[1]["kb"], x[1]["id"]))
        for _, rule, why in scored:
            if len(selected) >= limit:
                break
            add(rule, why)

        out = []
        for rule in selected:
            item = dict(rule)
            item["gist"] = rule.get("principle") or rule.get("gist") or rule.get("body", "")[:120]
            item["why"] = reasons.get(rule["id"], "")
            item["examples"] = (rule.get("examples") or [])[:2]
            out.append(item)
        return out

    def select_case(
        self,
        profile: TextProfile,
        text: str,
        limit: int = 1,
        lang_pair: str | None = None,
        poetry: bool = False,
        domain_requested: str | None = None,
    ) -> dict[str, Any] | None:
        """挑一条整段参考译例。

        domain_requested 是用户在界面上手选的领域（default=没选）：
        - 手选具体领域：只允许该领域的案例，且直接给最高权重；
        - default：维持原有打分（文本类型/标签/关键词重合），不做偏置；
        - 自动识别：按 profile.domain 轻微加权，不改变规则。
        """
        if not self.cases:
            return None
        lang_pair = lang_pair or profile.lang_pair
        manual = (domain_requested or "default") not in {"default", "general", ""}
        source_tokens = set(tokenize(text[:4000]))
        active = {k for k, v in profile.features.items() if v}
        scored = []
        for case in self.cases:
            if not case.get("enabled", True) or case.get("lang_pair", "en-zh") not in {lang_pair, "both"}:
                continue
            tags = set(case.get("tags", []))
            # 诗歌译例只在诗歌模式下用，避免拿诗的行文去套叙事/议论文本
            if (tags & {"poetry", "verse"}) and not poetry:
                continue
            case_domain = str(case.get("domain") or "general")
            if manual:
                # 手选了领域：其它领域全部停用，本领域给足权重
                if case_domain != domain_requested:
                    continue
                score = 100.0
            else:
                score = 0.0
                if profile.domain not in {"general", "default", ""} and case_domain == profile.domain:
                    score += 2.0
            if case.get("text_type") == profile.text_type:
                score += 4.0
            if profile.secondary_type and case.get("text_type") == profile.secondary_type:
                score += 1.5
            hit = tags & (active | {profile.domain.lower()})
            score += min(3.0, len(hit) * 1.2)
            case_tokens = set(tokenize(case.get("zh" if lang_pair == "zh-en" else "en", "")[:1200]))
            if source_tokens and case_tokens:
                overlap = len(source_tokens & case_tokens) / max(1, len(case_tokens))
                score += min(3.0, overlap * 9)
            threshold = 0.0 if manual else 3.5
            if score >= threshold:
                scored.append((score, case))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]

    def build_glossary(
        self,
        text: str,
        user_terms: str = "",
        limit: int = 20,
        lang_pair: str = "en-zh",
        domain_requested: str = "default",
    ) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        seen: set[str] = set()

        for line in (user_terms or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^(.+?)\s*(?:=|＝|：|:)\s*(.+)$", line)
            if not m:
                continue
            left, right = m.group(1).strip(), m.group(2).strip()
            if not left or not right:
                continue
            if lang_pair == "zh-en":
                zh, en = left, right
            else:
                en, zh = left, right
            found.append({"en": en, "zh": zh, "source": "user"})
            seen.add(en.lower())

        ac, entries = self._terms_ac(lang_pair)
        key = "zh" if lang_pair == "zh-en" else "en"
        hits: dict[int, int] = {}
        for idx, start, end in ac.find(text):
            if idx < 0 or idx >= len(entries):
                continue
            term = str(entries[idx].get(key, ""))
            if not term:
                continue
            # ASCII 术语做词边界校验，避免 "art" 命中 "start"
            if term.isascii() and term[:1].isalnum():
                before = text[start - 1] if start > 0 else " "
                after = text[end] if end < len(text) else " "
                if before.isalpha() or after.isalpha():
                    continue
            hits[idx] = hits.get(idx, 0) + 1

        seen_zh: set[str] = set()
        manual = (domain_requested or "default") not in {"default", "general", ""}
        ranked = sorted(
            hits.items(),
            key=lambda kv: (
                0 if manual and str(entries[kv[0]].get("domain") or "general") == domain_requested else 1,
                -len(str(entries[kv[0]].get(key, ""))),
                -kv[1],
            ),
        )
        for idx, _count in ranked:
            entry = entries[idx]
            entry_domain = str(entry.get("domain") or "general")
            # 没有领域归属的术语视为全局可用，不因用户选了某个领域而被排除。
            if manual and entry_domain not in {"general", "default", "all", "", domain_requested}:
                continue
            en = str(entry["en"])
            zh = str(entry["zh"])
            k = en.lower()
            if k in seen or (lang_pair == "en-zh" and (len(en) < 2 or k in GLOSSARY_STOP)):
                continue
            if lang_pair == "zh-en" and zh in seen_zh:
                continue
            found.append({"en": en, "zh": zh, "source": entry.get("source", "KB5")})
            seen.add(k)
            seen_zh.add(zh)
            if len(found) >= limit:
                break
        return found[:limit]

    def global_glossary(self, text: str, limit: int = 200, lang_pair: str = "en-zh") -> list[dict[str, str]]:
        """全文扫一遍，按出现次数挑出该文档要统一译名的术语（对齐 P1.3）。"""
        ac, entries = self._terms_ac(lang_pair)
        key = "zh" if lang_pair == "zh-en" else "en"
        counts: dict[int, int] = {}
        for idx, start, end in ac.find(text):
            if idx < 0 or idx >= len(entries):
                continue
            term = str(entries[idx].get(key, ""))
            if term.isascii() and term[:1].isalnum():
                before = text[start - 1] if start > 0 else " "
                after = text[end] if end < len(text) else " "
                if before.isalpha() or after.isalpha():
                    continue
            counts[idx] = counts.get(idx, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(str(entries[kv[0]].get(key, "")))))
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for idx, _c in ranked:
            e = entries[idx]
            k = str(e["en"]).lower()
            if k in seen:
                continue
            seen.add(k)
            out.append({"en": str(e["en"]), "zh": str(e["zh"]), "source": e.get("source", "KB5")})
            if len(out) >= limit:
                break
        return out




def _contains_term(text: str, term: str) -> bool:
    term = term.strip()
    if not term:
        return False
    flags = 0 if (term.isupper() and len(term) <= 6) else re.IGNORECASE
    pattern = r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])"
    return re.search(pattern, text, flags) is not None


QA_PRIORITY = {
    "KB2-005": 10, "KB4-017": 10, "KB2-003": 9, "KB2-027": 9, "KB2-014": 9,
    "KB4-016": 9, "KB2-011": 8, "KB4-012": 8, "KB2-012": 8, "KB4-013": 8,
    "KB2-001": 7, "KB2-002": 7, "KB4-006": 7, "KB4-015": 7, "KB4-018": 7,
    "KB1-006": 6, "KB1-009": 6, "KB5-001": 6, "KB5-002": 6, "KB2-033": 6,
    "KB2-036": 5, "KB2-037": 5, "KB3-010": 5, "KB3-011": 5, "KB3-012": 5,
    "KB3-013": 5, "KB2-006": 5, "KB2-021": 5,
}


def build_qa_checks(rules: list[dict[str, Any]], limit: int = 10, lang_pair: str = "en-zh") -> list[dict[str, str]]:
    checks: list[dict[str, str]] = [
        {"id": "CHK-FID", "title": "忠实", "text": "有无漏译、增译、数字、方位或专名错误？"},
        {"id": "CHK-FLU", "title": "通顺", "text": "有无搭配不当、代词冗余、句子断裂或翻译腔？"},
        # 三类恶性错误，任何文本都查
        {"id": "CHK-GENDER", "title": "性别人称", "text": "she/her 是否被译成“他”？him/her 单数是否被译成“他们”？同一人物前后性别、称谓是否一致？"},
        {"id": "CHK-REPEAT", "title": "重复", "text": "有无同一名词或同一句话连着重复两遍凑句？"},
        {"id": "CHK-COLLOC", "title": "生造搭配", "text": "有无“上升森林/筛落似的云/动作里没有疾驰”这类硬拼搭配？"},
    ]
    if lang_pair == "zh-en":
        checks[1]["text"] = "英文是否自然，有无中式英语、缺主语、逗号串句或逐字硬译？"
        checks.append({"id": "CHK-ENG", "title": "英语地道", "text": "动词、冠词、介词、单复数与搭配是否符合英语习惯？"})
    else:
        checks.append({"id": "CHK-REG", "title": "语域", "text": "叙述与对话的语域是否分层且全篇统一？"})
        checks.append({"id": "CHK-DASH", "title": "破折号",
                       "text": "同一段是否出现一个以上破折号？有没有把 A—B—A 插入结构直译成“他——一个……的人——突然……”？能否拆句或换标点？"})
        checks.append({"id": "CHK-PRON", "title": "代词回原文",
                       "text": "them/they/their 指代的人或物是否与原文一致？有没有被译文里邻近的 she/he 带偏？"})
        checks.append({"id": "CHK-DIALOG", "title": "对话分段",
                       "text": "多人长对话中，不同说话人是否按汉语习惯分段？无法判断说话人时是否保持了原顺序？"})
        checks.append({"id": "CHK-NEG", "title": "否定回声",
                       "text": "有没有“一成不变的不信任”这类两个否定词挨在一起的写法？"})
        checks.append({"id": "CHK-DUPWORD", "title": "短距离重复",
                       "text": "同一小句或相邻两句里，有没有同一个字/词连用两次（尤其“从、的、了、是、在”）？是否已调序或换词？"})
        checks.append({"id": "CHK-REDUND", "title": "语义重复",
                       "text": "修饰语是否重复了中心词的意思（“矛盾地自相矛盾”“十分完美”）？"})
        checks.append({"id": "CHK-NAME", "title": "人名完整",
                       "text": "人名是否整体音译？有没有“埃lnora”这类汉字+拉丁混排？"})
        checks.append({"id": "CHK-CULT", "title": "文化典故",
                       "text": "chocolate arabesques、Cudjoe 这类文化/种族背景词，是否只做了字面直译而没有交代含义？"})
    for rule in rules:
        checks.append({"id": rule["id"], "title": rule["title"], "text": rule.get("check") or ""})
    checks.sort(key=lambda c: -QA_PRIORITY.get(c["id"], 4))
    # 保留固定检查项，其余按优先级取
    head = [c for c in checks if c["id"].startswith("CHK-")]
    rest = [c for c in checks if not c["id"].startswith("CHK-")]
    return (head + rest)[:limit]


def estimate_baseline(profile: TextProfile, per_word: float = 66.3, fixed: int = 0) -> int:
    return int(profile.word_count * per_word + fixed)



