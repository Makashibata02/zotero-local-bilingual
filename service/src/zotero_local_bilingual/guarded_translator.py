from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from babeldoc.translator.translator import OpenAITranslator
from .translation_policy import TranslationOptions, role_prompt, thinking_setting, REFERENCE_POLICY
from .atomic_io import write_json_atomic
from .languages import RULE_VERSION


class GuardedTranslator(OpenAITranslator):
    # Separate namespace prevents reuse of old empty or truncated cached responses.
    name = "zlb-guard-v1"

    def __init__(self, spec: dict, api_key: str):
        self.policy = TranslationOptions.model_validate(spec)
        self.rate_path = spec.get("rate_database")
        self.rate_profile = spec.get("profile_id", "default")
        self.rate_qps = spec.get("qps", 2)
        super().__init__(
            self.policy.source_language, self.policy.target_language, spec["model"], base_url=spec["base_url"], api_key=api_key,
            ignore_cache=self.policy.ignore_cache,
            send_temperature=not spec.get("no_send_temperature", False),
            thinking=thinking_setting(spec["base_url"], self.policy.thinking_mode),
        )
        self.client = self.client.with_options(max_retries=0, timeout=90)
        self.add_cache_impact_parameters("endpoint", spec["base_url"])
        self.add_cache_impact_parameters("role", role_prompt(self.policy.custom_system_prompt, self.policy.target_language))
        self.add_cache_impact_parameters("rule_version", RULE_VERSION)
        if self.policy.glossary_text:
            self.add_cache_impact_parameters("glossary_text", self.policy.glossary_text)
        self.add_cache_impact_parameters("max_output_tokens", self.policy.max_output_tokens)
        self.lock = threading.Lock()
        self.requests = []
        self.pending_tokens = 0
        self.unconfirmed_tokens = 0
        self.previous_failures = []
        self.failure = None
        self.failure_reason = None
        self.required_budget_tokens = None
        self.audit_path = Path(spec["engine_working_dir"]) / "api-usage.json"
        self._restore_audit()

    def _restore_audit(self):
        """Keep this job's lifetime spend across explicit retry and restart."""
        if not self.audit_path.exists():
            return
        try:
            saved = json.loads(self.audit_path.read_text("utf8"))
            if not isinstance(saved, dict):
                raise ValueError("not an object")
            counters = {}
            for key in ("total_tokens", "prompt_tokens", "completion_tokens",
                        "cache_hit_prompt_tokens", "request_count", "cache_hits"):
                value = saved[key]
                if type(value) is not int or value < 0:
                    raise ValueError("invalid counter")
                counters[key] = value
            rows = saved["requests"]
            if not isinstance(rows, list) or len(rows) != counters["request_count"]:
                raise ValueError("inconsistent request count")
            known_prompt = known_completion = known_total = uncertain = 0
            for number, row in enumerate(rows, 1):
                if not isinstance(row, dict) or row.get("number") != number:
                    raise ValueError("invalid request history")
                if row.get("status") not in {"running", "ok", "failed", "interrupted"}:
                    raise ValueError("invalid request status")
                prompt = row.get("prompt_tokens")
                completion = row.get("completion_tokens")
                if type(prompt) is int and prompt >= 0 and type(completion) is int and completion >= 0:
                    total = row.get("total_tokens", prompt + completion)
                    if type(total) is not int or total < prompt + completion:
                        raise ValueError("invalid request usage")
                    known_prompt += prompt
                    known_completion += completion
                    known_total += total
                else:
                    # The provider may have billed a request interrupted before
                    # its response arrived. Retain the pre-request reservation.
                    reserve = row.get("reserved_tokens")
                    if type(reserve) is not int or reserve <= 0:
                        raise ValueError("unaccounted historical request")
                    uncertain += reserve
                if row["status"] == "running":
                    row["status"] = "interrupted"
            if (counters["total_tokens"] < known_total
                    or counters["prompt_tokens"] < known_prompt
                    or counters["completion_tokens"] < known_completion
                    or counters["total_tokens"] < counters["prompt_tokens"] + counters["completion_tokens"]):
                raise ValueError("inconsistent token history")
            prior = saved.get("previous_failures", [])
            if not isinstance(prior, list) or any(not isinstance(x, str) for x in prior):
                raise ValueError("invalid failure history")
            if saved.get("failure"):
                if not isinstance(saved["failure"], str):
                    raise ValueError("invalid failure")
                prior = [*prior, saved["failure"]]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Do not overwrite damaged evidence or silently start a new budget.
            raise RuntimeError("历史用量审计损坏或不完整，无法安全恢复累计预算；任务已停止，原审计文件保留。") from exc
        self.requests = rows
        self.unconfirmed_tokens = uncertain
        self.previous_failures = prior
        self.token_count.inc(counters["total_tokens"])
        self.prompt_token_count.inc(counters["prompt_tokens"])
        self.completion_token_count.inc(counters["completion_tokens"])
        self.cache_hit_prompt_token_count.inc(counters["cache_hit_prompt_tokens"])
        self.translate_cache_call_count = counters["cache_hits"]

    def snapshot(self):
        return {
            "schema_version": 2,
            "total_tokens": self.token_count.value,
            "prompt_tokens": self.prompt_token_count.value,
            "completion_tokens": self.completion_token_count.value,
            "cache_hit_prompt_tokens": self.cache_hit_prompt_token_count.value,
            "request_count": len(self.requests),
            "cache_hits": self.translate_cache_call_count,
            "thinking": self.thinking,
            "failure": self.failure,
            "failure_reason": self.failure_reason,
            "required_budget_tokens": self.required_budget_tokens,
            "previous_failures": self.previous_failures,
            "unconfirmed_tokens": self.unconfirmed_tokens,
            "pending_tokens": self.pending_tokens,
            "budget_total_tokens": self.token_count.value + self.unconfirmed_tokens + self.pending_tokens,
            "limits": {"max_total_tokens": self.policy.max_total_tokens,
                       "max_requests": self.policy.max_requests,
                       "max_output_tokens": self.policy.max_output_tokens},
            "requests": self.requests,
        }

    def save_audit(self):
        write_json_atomic(self.audit_path, self.snapshot(), indent=2)

    def _request(self, messages):
        # UTF-8 bytes are a conservative estimate, not an exact provider tokenizer.
        reserve = sum(len(m["content"].encode("utf8")) + 64 for m in messages) + self.policy.max_output_tokens
        with self.lock:
            if self.failure:
                raise RuntimeError(self.failure)
            if len(self.requests) >= self.policy.max_requests or self.token_count.value + self.unconfirmed_tokens + self.pending_tokens + reserve > self.policy.max_total_tokens:
                self.failure_reason = "budget_limit"
                self.required_budget_tokens = self.token_count.value + self.unconfirmed_tokens + self.pending_tokens + reserve
                self.failure = "达到任务 token 或请求次数上限；任务已暂停。请在工作台确认追加额度后继续。"
                self.save_audit()
                raise RuntimeError(self.failure)
            self.pending_tokens += reserve
            row = {"number": len(self.requests) + 1, "status": "running", "reserved_tokens": reserve}
            self.requests.append(row)
            # A crash after the provider received the request must not erase it.
            # Persist before sending; an audit write failure sends no request.
            try:
                self.save_audit()
            except OSError as exc:
                self.pending_tokens -= reserve
                row.update(status="failed", total_tokens=0, prompt_tokens=0, completion_tokens=0)
                self.failure = "无法保存请求前用量审计，已停止任务；未发送此模型请求。"
                raise RuntimeError(self.failure) from exc
        started = time.monotonic()
        try:
            kwargs = {"temperature": 0} if self.send_temperature else {}
            from .shared_rate import acquire
            acquire(self.rate_path, self.rate_profile, self.rate_qps)
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=self.policy.max_output_tokens,
                extra_body=self.extra_body, **kwargs,
            )
            choice = response.choices[0]
            content = choice.message.content or ""
            usage = response.usage
            with self.lock:
                self.update_token_count(response)
                details = getattr(usage, "completion_tokens_details", None)
                row.update({"finish_reason": choice.finish_reason,
                            "total_tokens": getattr(usage, "total_tokens", None),
                            "prompt_tokens": getattr(usage, "prompt_tokens", None),
                            "completion_tokens": getattr(usage, "completion_tokens", None),
                            "reasoning_tokens": getattr(details, "reasoning_tokens", None),
                            "content_characters": len(content)})
            if choice.finish_reason != "stop" or not content.strip():
                raise ValueError(f"模型未返回完整译文（finish_reason={choice.finish_reason}，正文长度={len(content)}）；停止任务以避免空白和重复扣费。")
            if usage is None or not usage.total_tokens:
                raise ValueError("服务商未返回 token 用量，无法执行用量保护，已停止任务。")
            row["status"] = "ok"
            return content.strip()
        except Exception as exc:
            with self.lock:
                # Do not persist response bodies, prompts or exception URLs/credentials.
                message = str(exc) if isinstance(exc, ValueError) else f"模型请求失败（{type(exc).__name__}），未自动重试。"
                self.failure = self.failure or message
                row["status"] = "failed"
            raise RuntimeError(self.failure) from exc
        finally:
            with self.lock:
                self.pending_tokens -= reserve
                if type(row.get("total_tokens")) is not int or row["total_tokens"] <= 0:
                    self.unconfirmed_tokens += reserve
                row["elapsed_seconds"] = round(time.monotonic() - started, 3)
                self.save_audit()

    def do_translate(self, text, rate_limit_params=None):
        messages = self.prompt(text)
        messages[0]["content"] = role_prompt(self.policy.custom_system_prompt, self.policy.target_language)
        return self._request(messages)

    def do_llm_translate(self, text, rate_limit_params=None):
        if text is None:
            return None
        result = self._request([
            {"role": "system", "content": role_prompt(self.policy.custom_system_prompt, self.policy.target_language)},
            {"role": "user", "content": text},
        ])
        if (rate_limit_params or {}).get("request_json_mode"):
            # BabelDOC can retry schema mismatches; never let empty fields enter its cache.
            try:
                cleaned = result.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
                parsed = json.loads(cleaned)
                rows = parsed if isinstance(parsed, list) else [parsed]
                is_glossary = text.lstrip().startswith("You are an expert multilingual terminologist.")
                fields = ("src", "tgt") if is_glossary else ("output",)
                if (not rows and not is_glossary) or any(
                    not isinstance(r, dict) or any(not isinstance(r.get(k), str) or not r[k].strip() for k in fields)
                    for r in rows
                ):
                    raise ValueError("empty translation field")
            except (ValueError, TypeError, KeyError) as exc:
                with self.lock:
                    self.failure = "模型返回无效 JSON 或空译文；已停止任务，避免反复回退扣费。"
                    self.save_audit()
                raise RuntimeError(self.failure) from exc
        return result
