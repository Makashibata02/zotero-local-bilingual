"""Anonymous experimental web adapters; no paid fallback or document truncation."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import httpx
from babeldoc.translator.translator import BaseTranslator
from .atomic_io import write_json_atomic
from .languages import RULE_VERSION

MARKER = re.compile(r"\{v\d+\}|<style id='\d+'>|</style>")


def split_text(text: str, limit: int = 800) -> list[str]:
    """Keep every character, including whitespace, and never split a marker."""
    parts = []
    while len(text) > limit:
        cut = limit
        for marker in MARKER.finditer(text):
            if marker.start() < cut < marker.end():
                cut = marker.start()
                break
        boundary = list(re.finditer(r"[.!?;\n]\s+", text[:cut]))
        if not boundary or boundary[-1].end() <= cut // 2:
            boundary = list(re.finditer(r"\s+", text[:cut]))
        if boundary and boundary[-1].end() > cut // 2:
            cut = boundary[-1].end()
        if cut <= 0:
            raise ValueError("占位符超过字符限制")
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts


class WebProvider:
    def __init__(self, provider: str, client=None, target_language="zh-CN"):
        self.provider = provider
        from .languages import LANGUAGES
        self.target_code = LANGUAGES[target_language][3 if provider == "bing" else 4]
        self.client = client or httpx.Client(trust_env=False, timeout=30,
            follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"})
        self.auth = None
        self.sequence = 0

    def translate(self, text: str) -> str:
        if self.provider == "bing":
            if self.auth is None:
                response = self.client.get("https://cn.bing.com/translator")
                response.raise_for_status()
                html = response.text
                ig = re.search(r'IG:"([^"]+)"', html) or re.search(r'"ig":"([^"]+)"', html)
                iid = re.findall(r'data-iid="([^"]+)"', html)
                auth = re.search(r'params_AbusePreventionHelper\s*=\s*\[(\d+),"([^"]+)"', html)
                if not ig or not iid or not auth:
                    raise RuntimeError("Bing 页面接口已改变或需要验证；请手动换用其他服务")
                self.auth = (ig[1], iid[-1], auth[1], auth[2])
            ig, iid, key, token = self.auth
            self.sequence += 1
            response = self.client.post("https://cn.bing.com/ttranslatev3",
                params={"isVertical": "1", "IG": ig, "IID": iid + "." + str(self.sequence)},
                data={"fromLang": "en", "to": self.target_code, "text": text, "key": key, "token": token})
            response.raise_for_status()
            body = response.json()
            try:
                output = body[0]["translations"][0]["text"]
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError("Bing 限流、验证码或响应变化；任务停止，请手动换用其他服务") from exc
        elif self.provider == "tencent":
            response = self.client.post("https://transmart.qq.com/api/imt", json={
                "header": {"fn": "auto_translation", "client_key": f"browser-{int(time.time()*1000)}"},
                "type": "plain", "model_category": "normal",
                "source": {"lang": "en", "text_list": [text]}, "target": {"lang": self.target_code}})
            response.raise_for_status()
            body = response.json()
            output = body.get("auto_translation")
            if not isinstance(output, list) or len(output) != 1:
                raise RuntimeError("腾讯交互翻译限流或响应变化；任务停止，请手动换用其他服务")
            output = output[0]
        else:
            raise ValueError("未知免费翻译服务")
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("免费服务返回空译文，未交付 PDF")
        return output


class FreeTranslator(BaseTranslator):
    name = "zlb-web-v1"
    def __init__(self, spec: dict, provider=None, interval: float = 2):
        self.target_language = spec.get("target_language", "zh-CN")
        super().__init__("en", self.target_language, spec.get("ignore_cache", False))
        self.provider = provider or WebProvider(spec["provider"], target_language=self.target_language)
        self.model = spec["provider"]
        self.add_cache_impact_parameters("provider", self.model)
        self.add_cache_impact_parameters("rule_version", RULE_VERSION)
        self.lock = threading.RLock()
        self.interval = max(0, interval)
        self.last_request = 0.0
        self.request_limit = spec.get("free_max_requests", 2000)
        self.character_limit = spec.get("max_characters", 1000000)
        self.audit_path = Path(spec["engine_working_dir"]) / "api-usage.json"
        self.cache_path = self.audit_path.with_name("free-cache.json")
        self.failure = None
        self.requests = []
        self.characters = 0
        self.local_cache = {}
        if self.audit_path.exists():
            saved = json.loads(self.audit_path.read_text("utf8"))
            if saved.get("usage_kind") != "characters" or saved.get("provider") != self.model:
                raise RuntimeError("历史免费用量类型不符，不能重置累计预算")
            if saved.get("target_language", "zh-CN") != self.target_language:
                raise RuntimeError("历史目标语言不符，不能复用用量和译文缓存")
            self.requests = saved["requests"]
            if (len(self.requests) != saved["request_count"] or
                any(type(r.get("characters")) is not int or r["characters"] < 0 for r in self.requests)):
                raise RuntimeError("历史用量审计损坏，不能恢复")
            self.characters = sum(r["characters"] for r in self.requests)
            if self.characters != saved["characters"]:
                raise RuntimeError("历史字符审计不一致")
        if self.cache_path.exists():
            self.local_cache = json.loads(self.cache_path.read_text("utf8"))

    def do_llm_translate(self, text, rate_limit_params=None):
        raise NotImplementedError  # Select BabelDOC's traditional MT implementation.

    def get_formular_placeholder(self, placeholder_id):
        return "{v" + str(placeholder_id) + "}", r"\{\s*v\s*" + str(placeholder_id) + r"\s*\}"

    def get_rich_text_left_placeholder(self, placeholder_id):
        return f"<style id='{placeholder_id}'>", r"<\s*style\s*id\s*=\s*'\s*" + str(placeholder_id) + r"\s*'\s*>"

    def get_rich_text_right_placeholder(self, placeholder_id):
        return "</style>", r"<\s*\/\s*style\s*>"

    def snapshot(self):
        return {"usage_kind": "characters", "provider": self.model,
            "source_language": "en", "target_language": self.target_language, "rule_version": RULE_VERSION,
            "request_count": len(self.requests), "characters": self.characters,
            "requests": self.requests, "failure": self.failure,
            "limits": {"requests": self.request_limit, "characters": self.character_limit}}

    def save_audit(self):
        write_json_atomic(self.audit_path, self.snapshot(), indent=2)

    def _request(self, text):
        digest = hashlib.sha256((self.model + "\0en\0" + self.target_language + "\0" + RULE_VERSION + "\0" + text).encode()).hexdigest()
        if not self.ignore_cache and digest in self.local_cache:
            return self.local_cache[digest]
        if len(self.requests) >= self.request_limit or self.characters + len(text) > self.character_limit:
            raise RuntimeError("免费翻译累计请求/字符上限已到；不会切换收费 API")
        time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
        row = {"characters": len(text), "status": "running"}
        self.requests.append(row)
        self.characters += len(text)
        self.save_audit()  # Reserve before network; interruptions cannot reset usage.
        self.last_request = time.monotonic()
        try:
            output = self.provider.translate(text)
            if not output.strip():
                raise RuntimeError("空译文")
            row["status"] = "ok"
            self.local_cache[digest] = output
            write_json_atomic(self.cache_path, self.local_cache)
            self.save_audit()
            return output
        except Exception:
            row["status"] = "failed"
            self.save_audit()
            raise

    def do_translate(self, text, rate_limit_params=None):
        with self.lock:
            if self.failure:
                raise RuntimeError(self.failure)
            try:
                names = {}
                def protect_author(match):
                    marker = "{v" + str(9000000+len(names)) + "}"
                    while marker in text or marker in names: marker = "{v" + str(int(marker[2:-1])+1) + "}"
                    names[marker] = match[2] + (match[3] or "")
                    return match[1] + marker
                text = re.sub(r"(\bAuthors?:\s*)([^.!?\n]+)([.!?](?:\s+|$))?", protect_author, text, flags=re.I)
                output = "".join(self._request(part) for part in split_text(text))
                if Counter(MARKER.findall(output)) != Counter(MARKER.findall(text)):
                    # One deterministic fallback, translating prose only.
                    pieces = re.split("(" + MARKER.pattern + ")", text)
                    output = "".join(piece if MARKER.fullmatch(piece) or not piece.strip()
                        else "".join(self._request(part) for part in split_text(piece)) for piece in pieces)
                if Counter(MARKER.findall(output)) != Counter(MARKER.findall(text)):
                    raise RuntimeError("公式或样式占位符损坏，未交付 PDF")
                for marker, original in names.items():
                    output = output.replace(marker, original)
                return output
            except Exception as exc:
                self.failure = f"免费翻译停止：{type(exc).__name__}：{str(exc)[:300]}"
                self.save_audit()
                raise RuntimeError(self.failure) from exc
