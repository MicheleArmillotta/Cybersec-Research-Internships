"""Keyword-based classification.

Philosophy: better a false positive (e.g. a generic SWE internship) than a false
negative (missing a security internship). Patterns are therefore deliberately broad.
"""
import re

# A posting is kept only if it looks like an internship / student / PhD / fellowship role.
LEVEL_PATTERNS = [
    r"\bintern(ship|ships|s)?\b",
    r"\bco-?op\b",
    r"\bsummer\b",
    r"\bstudent\b",
    r"\bph\.?d\b",
    r"\bfellow(ship|ships|s)?\b",
    r"\bworking student\b",
    r"\bwerkstudent",
    r"\bpraktik",
    r"\bstagiaire\b",
    r"\bstage\b",
    r"\btirocin",
    r"\bstagista\b",
    r"\bgraduate (scheme|programme|program)\b",
    r"\bearly careers?\b",
    r"\bthesis\b",
    r"\bapprentice",
    r"\bresidency\b",
    r"\bstudentship\b",
]

CYBER_PATTERNS = [
    r"secur", r"cyber", r"vulnerab", r"pentest", r"penetration", r"red team", r"blue team",
    r"purple team", r"offensive", r"malware", r"threat", r"fuzz", r"exploit", r"reverse",
    r"appsec", r"app sec", r"cryptograph", r"\bcrypto\b", r"privacy", r"trust (&|and) safety",
    r"\bsoc\b", r"detection", r"incident", r"forensic", r"infosec", r"hacker", r"hacking", r"adversar",
    r"assurance", r"\bsiem\b", r"\biam\b", r"identity", r"zero trust", r"firmware", r"binary",
    r"program analysis", r"static analysis", r"dynamic analysis", r"\bctf\b", r"\bgrc\b",
    r"abuse", r"integrity", r"\bsafety\b", r"\bsigint\b", r"intrusion",
    r"defen[cs]e", r"resilien", r"attack", r"protect",
]

RESEARCH_PATTERNS = [
    r"research", r"scientist", r"ph\.?d", r"fellow", r"\blab\b", r"\blabs\b", r"r&d",
    r"\bscience\b", r"\bpostdoc", r"\bthesis\b", r"\bacademic",
]

AI_PATTERNS = [
    r"\bai\b", r"\bml\b", r"machine learning", r"deep learning", r"\bllm", r"language model",
    r"\bgenai\b", r"generative", r"\bnlp\b", r"neural", r"\bagent",
]

_LEVEL = re.compile("|".join(LEVEL_PATTERNS), re.I)
_CYBER = re.compile("|".join(CYBER_PATTERNS), re.I)
_RESEARCH = re.compile("|".join(RESEARCH_PATTERNS), re.I)
_AI = re.compile("|".join(AI_PATTERNS), re.I)


def is_internship(text: str) -> bool:
    return bool(_LEVEL.search(text or ""))


def tags_for(text: str) -> list[str]:
    tags = []
    if _CYBER.search(text or ""):
        tags.append("cyber")
    if _RESEARCH.search(text or ""):
        tags.append("research")
    if _AI.search(text or ""):
        tags.append("ai")
    if not tags:
        tags.append("other")
    return tags
