"""简历解析：把简历文本切成有语义的板块，供分块入库与检索使用。

为什么不能直接整篇丢进分块器？（面试考点）
1. 简历的板块天然是检索单元：问"项目经历"时召回"教育经历"是噪声；
2. 保留板块名能让块自带上下文——块被单独召回时，LLM 也知道它属于哪一部分；
3. 有些板块（如技能清单）本身就是列表，硬切会切断语义。

实现策略：先用标题正则识别板块边界，再对每个板块独立分块并加【板块】前缀。
PDF 抽取常丢标题层级，此时自动降级为全文分块（section=全文），保证不会解析失败。
"""
import re
from dataclasses import dataclass, field

# 板块识别规则：标题行较短 + 命中关键词。顺序即优先级
SECTION_PATTERNS: list[tuple[str, str]] = [
    ("基本信息", r"(基本信息|个人信息|联系方式|个人资料|基本资料)"),
    ("教育经历", r"(教育经历|教育背景|学历背景|学历|教育与培训)"),
    ("专业技能", r"(专业技能|技能清单|技能特长|技术栈|掌握技能|技能)"),
    ("工作经历", r"(工作经历|实习经历|工作经验|职业经历|工作履历)"),
    ("项目经历", r"(项目经历|项目经验|项目介绍|主要项目|项目)"),
    ("校园经历", r"(校园经历|社团经历|学生工作|社会实践)"),
    ("荣誉奖项", r"(荣誉奖项|获奖情况|所获奖励|奖项|荣誉)"),
    ("自我评价", r"(自我评价|个人评价|自我介绍|个人总结|其他信息)"),
]
_EN_VERSION = {
    "Working Experience": "工作经历",
    "Work Experience": "工作经历",
    "Experience": "工作经历",
    "Projects": "项目经历",
    "Project": "项目经历",
    "Education": "教育经历",
    "Skills": "专业技能",
    "Skill": "专业技能",
    "Awards": "荣誉奖项",
    "Honors": "荣誉奖项",
    "Summary": "自我评价",
    "About": "自我评价",
}

# 标题行特征：长度短、可带 markdown 井号/序号/项目符号装饰
# 注意：只剥装饰字符（# > * 数字序号 中文序号 括号），绝不能剥中文正文
_DECOR = r"[\s#>*•·●■□\-–—]"
_TITLE_CLEAN = re.compile(
    rf"^(?:{_DECOR})*"
    r"(?:(?:[0-9]{1,2}\s*[.、)）])|(?:[一二三四五六七八九十]+\s*[、.．]))?"
    rf"[\s:：]*"
)
_MAX_TITLE_LEN = 24
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_YEARS = re.compile(r"(\d{4})\s*[-–~至]\s*(\d{4}|\d{1,2}|至今|present)", re.I)
# 姓名分隔符：中英文简历常见 "张明 · AI Agent 开发工程师"
_NAME_SEP = re.compile(r"[·•|｜,，/、\s\-–—]+")


@dataclass
class ResumeSection:
    name: str
    content: str

    def to_dict(self) -> dict:
        return {"name": self.name, "content": self.content}


@dataclass
class ResumeProfile:
    """简历结构化结果。"""

    name: str = ""
    contact: dict[str, str] = field(default_factory=dict)
    sections: list[ResumeSection] = field(default_factory=list)
    raw: str = ""
    structured: bool = True  # PDF 丢标题时为 False（走了降级分支）

    def section(self, name: str) -> str:
        for s in self.sections:
            if s.name == name:
                return s.content
        return ""

    @property
    def section_names(self) -> list[str]:
        return [s.name for s in self.sections]

    def to_blocks(self, chunk_size: int = 500, overlap: int = 120) -> list[dict]:
        """按板块分块，返回 [{text, section}]；text 带【板块】前缀便于检索命中后自解释。"""
        from app.rag.chunker import chunk_text

        blocks: list[dict] = []
        for sec in self.sections:
            for piece in chunk_text(sec.content, chunk_size=chunk_size, overlap=overlap):
                blocks.append({"text": f"【{sec.name}】{piece}", "section": sec.name})
        return blocks


def _match_heading(line: str) -> str | None:
    """判断一行是否为板块标题，返回规范化板块名。"""
    raw = line.strip()
    if not raw or len(raw) > _MAX_TITLE_LEN:
        return None
    title = _TITLE_CLEAN.sub("", raw).strip().rstrip(":：").strip()
    if not title or len(title) > _MAX_TITLE_LEN:
        return None
    # 英文标题（常见于英文简历 / 模板）
    for en, zh in _EN_VERSION.items():
        if title.lower() == en.lower():
            return zh
    for zh_name, pattern in SECTION_PATTERNS:
        # 标题应"几乎只由板块词构成"，避免把正文里出现"项目"的句子当标题
        if re.fullmatch(rf"[\s\W]*{pattern}[\s\W]*", title):
            return zh_name
    return None


def _name_token(line: str) -> str:
    """从一行里取首个候选姓名 token（剥装饰 + 按分隔符切）。"""
    clean = _TITLE_CLEAN.sub("", line).strip()
    if not clean:
        return ""
    return _NAME_SEP.split(clean)[0].strip()


def _extract_name(lines: list[str]) -> str:
    """姓名抽取：优先 markdown H1，其次开头几个"短且不像信息行"的行。"""
    for ln in lines[:6]:
        if re.match(r"^#{1,2}\s+\S", ln.strip()):
            token = _name_token(ln)
            if token:
                return token[:20]
    for ln in lines[:8]:
        s = ln.strip()
        if not s or _match_heading(s):
            continue
        if _EMAIL.search(s) or _PHONE.search(s):
            continue
        # 含冒号的通常是"求职意向：xx"这类信息行，含长数字的是学号/年份行
        if re.search(r"[:：]", s) or re.search(r"\d{3,}", s):
            continue
        token = _name_token(s)
        if 2 <= len(token) <= 20:
            return token
    return ""


def parse_resume(text: str) -> ResumeProfile:
    """解析简历文本。识别不到任何板块时降级为全文单板块。"""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    profile = ResumeProfile(raw=text)

    profile.name = _extract_name(lines)
    email = _EMAIL.search(text)
    phone = _PHONE.search(text)
    if email:
        profile.contact["email"] = email.group(0)
    if phone:
        profile.contact["phone"] = phone.group(1)

    # 按标题切板块
    current: str | None = None
    buffer: list[str] = []
    sections: list[ResumeSection] = []

    def flush() -> None:
        if current and buffer:
            content = "\n".join(buffer).strip()
            if content:
                sections.append(ResumeSection(current, content))

    for ln in lines:
        heading = _match_heading(ln)
        if heading:
            flush()
            current = heading
            buffer = []
            continue
        if current:
            buffer.append(ln)
    flush()

    if not sections:
        profile.structured = False
        profile.sections = [ResumeSection("全文", text.strip())]
    else:
        profile.sections = sections
    return profile


def resume_summary(profile: ResumeProfile) -> dict:
    """给日志/前端用的摘要。"""
    return {
        "name": profile.name,
        "contact": profile.contact,
        "structured": profile.structured,
        "sections": [
            {"name": s.name, "chars": len(s.content), "preview": s.content[:60]}
            for s in profile.sections
        ],
        "year_spans": [f"{a}-{b}" for a, b in _YEARS.findall(profile.raw)][:3],
    }
