"""Model management mixin — multi-model support, routing, failover chains."""

import json

from i18n import t
from theme import C
from llm import LLMClient, LLMError


class ModelMixin:
    """Model management methods for REPL.

    Expects self to have: config, _models_path, _models, _routing,
    active_model, _model_display, _manual_model, llm, context,
    tool_ctx, log, _use_compact_tools.
    """

    # Known input context limits for auto-detection
    _KNOWN_LIMITS = {
        "api.deepseek.com": 128000,  # V3: 64K input, V4: 128K+ (conservative)
        "api.openai.com": 128000,    # GPT-4o: 128K
        "openrouter.ai": 128000,     # varies by model
    }

    def _load_models(self) -> dict:
        """Load models and routing from .emelya/models.json."""
        models = {
            "local": {
                "base_url": self.config.base_url,
                "model": self.config.model,
            }
        }
        self._routing = {}  # operation → [model_name, ...]
        if self._models_path.exists():
            try:
                data = json.loads(self._models_path.read_text(encoding="utf-8"))
                models.update(data.get("models", {}))
                self._routing = data.get("routing", {})
            except (json.JSONDecodeError, OSError):
                pass
        return models

    def _save_models(self):
        """Save models config (excluding 'local' which comes from CLI)."""
        to_save = {k: v for k, v in self._models.items() if k != "local"}
        data = {"models": to_save}
        if self._routing:
            data["routing"] = self._routing
        self._models_path.parent.mkdir(parents=True, exist_ok=True)
        self._models_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _get_client(self, model_name: str) -> LLMClient:
        """Get or create LLM client for a model by name. Cached."""
        if not hasattr(self, "_client_cache"):
            self._client_cache = {}
        if model_name in self._client_cache:
            return self._client_cache[model_name]
        cfg = self._models.get(model_name)
        if not cfg:
            return None
        client = LLMClient(
            base_url=cfg.get("base_url", self.config.base_url),
            model=cfg.get("model", "default"),
            api_key=cfg.get("api_key"),
        )
        self._client_cache[model_name] = client
        return client

    def _call_with_routing(self, operation: str, messages, tools, **kwargs):
        """Call LLM with failover chain based on routing config.
        Falls back to self.llm if no routing for operation."""
        chain = self._routing.get(operation, self._routing.get("default"))
        if not chain:
            # No routing — use active model directly
            if self.config.stream:
                return self.llm.stream_chat(messages, tools, **kwargs)
            return self.llm.chat(messages, tools, **kwargs)

        last_err = None
        for model_name in chain:
            client = self._get_client(model_name)
            if not client:
                continue
            try:
                # Quick health check (1s timeout)
                ok, _ = client.check_connection()
                if not ok:
                    continue
                # Use streaming for default operation
                if operation == "default" and self.config.stream:
                    resp = client.stream_chat(messages, tools, **kwargs)
                else:
                    resp = client.chat(messages, tools, **kwargs)
                # Update display name from response
                mid = resp.get("model_id")
                if mid:
                    display = mid
                    for suffix in (".gguf", ".bin"):
                        if display.endswith(suffix):
                            display = display[:-len(suffix)]
                    self._model_display = display
                return resp
            except Exception as e:
                last_err = e
                continue

        raise LLMError(f"All models in routing chain '{operation}' failed: {last_err}")

    def _resolve_client_for_operation(self, operation: str):
        """Resolve LLM client for a given operation using routing chain.
        Returns (client, model_name) or (self.llm, self.active_model)."""
        if self._manual_model or not self._routing:
            return self.llm, self.active_model
        chain = self._routing.get(operation, self._routing.get("default"))
        if not chain:
            return self.llm, self.active_model
        for model_name in chain:
            client = self._get_client(model_name)
            if not client:
                continue
            try:
                ok, _ = client.check_connection()
                if ok:
                    return client, model_name
            except Exception:
                continue
        return self.llm, self.active_model

    def _create_llm_client(self) -> LLMClient:
        """Create LLM client for the active model."""
        cfg = self._models.get(self.active_model)
        if not cfg:
            cfg = self._models.get("local", {})
            self.active_model = "local"
        # Apply context_limit: explicit > known provider > CLI default
        model_ctx_limit = cfg.get("context_limit")
        if not model_ctx_limit:
            base_url = cfg.get("base_url", "")
            for domain, limit in self._KNOWN_LIMITS.items():
                if domain in base_url:
                    model_ctx_limit = limit
                    break
        self.context.context_limit = model_ctx_limit or self.config.context_limit
        self._use_compact_tools = self.context.context_limit < 32000
        # Apply max_tokens: per-model > CLI default
        self.config.max_tokens = cfg.get("max_tokens") or self.config.max_tokens
        return LLMClient(
            base_url=cfg.get("base_url", self.config.base_url),
            model=cfg.get("model", "default"),
            api_key=cfg.get("api_key"),
        )

    def _switch_model(self, name: str):
        """Switch to a specific model. Disables routing (manual override)."""
        if name == "auto":
            # Re-enable routing
            self._manual_model = False
            print(f"  {C.info}Routing enabled{C.reset}")
            return
        if name not in self._models:
            print(f"  {t('unknown_model')}: {name}")
            self._list_models()
            return
        prev = self.active_model
        self.active_model = name
        self._model_display = name
        self._manual_model = True  # disable routing
        self.llm = self._create_llm_client()
        self.tool_ctx.llm_client = self.llm
        self.log.event(f"Model switched: {prev} -> {name} ({self.llm.base_url} / {self.llm.model})")
        ok, info = self.llm.check_connection()
        if ok:
            self._update_model_display(info)
            # Apply detected context limit if no explicit override
            detected = getattr(self.llm, "detected_context_limit", None)
            cfg = self._models.get(name, {})
            if detected and not cfg.get("context_limit"):
                self.context.context_limit = detected
            print(f"  {t('switched_to')}: {self._model_display}, ctx:{self.context.context_limit // 1000}K")
        else:
            print(f"  {t('switched_to')}: {name} ({t('warning')}: {info})")

    def _update_model_display(self, info: str = ""):
        """Extract short model name from connection info or API response."""
        # From check_connection: "models: Qwen3.5-4B-Q5_K_M.gguf"
        if info.startswith("models: "):
            name = info[8:].split(",")[0].strip()
            # Strip common suffixes for cleaner display
            for suffix in (".gguf", ".bin"):
                if name.endswith(suffix):
                    name = name[:-len(suffix)]
            self._model_display = name

    def _list_models(self):
        for name, cfg in self._models.items():
            active = " *" if name == self.active_model and self._manual_model else ""
            url = cfg.get("base_url", "?")
            model = cfg.get("model", "?")
            has_key = " [key]" if cfg.get("api_key") else ""
            ctx = cfg.get("context_limit")
            ctx_str = f" ctx:{ctx // 1000}K" if ctx else ""
            print(f"  {name}: {url} / {model}{has_key}{ctx_str}{active}")
        if self._routing:
            mode = "manual" if self._manual_model else "auto"
            print(f"  {C.muted}Routing: {mode} (/model auto to enable, /model <name> to override){C.reset}")
            if not self._manual_model:
                for op, chain in self._routing.items():
                    print(f"    {op}: {' → '.join(chain)}")

    def _model_add(self, arg: str):
        if not arg:
            print("  Usage: /model add <name> <base_url> <model> [api_key] [--context-limit N]")
            print("  Providers (base_url):")
            print("    Local llama.cpp:  http://localhost:8080")
            print("    OpenAI:           https://api.openai.com")
            print("    DeepSeek:         https://api.deepseek.com")
            print("    OpenRouter:       https://openrouter.ai/api")
            print("  Examples:")
            print("    /model add gpt4 https://api.openai.com gpt-4o sk-...")
            print("    /model add deepseek https://api.deepseek.com deepseek-chat sk-...")
            print("    /model add local8k http://localhost:8080 default --context-limit 8000")
            return

        parts = arg.split()
        # Extract --context-limit
        ctx_limit = None
        filtered = []
        i = 0
        while i < len(parts):
            if parts[i] == "--context-limit" and i + 1 < len(parts):
                try:
                    ctx_limit = int(parts[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                filtered.append(parts[i])
                i += 1

        if len(filtered) < 3:
            print("  Error: need at least name, base_url, and model")
            return

        name = filtered[0]
        base_url = filtered[1]
        model = filtered[2]
        api_key = filtered[3] if len(filtered) > 3 else None

        # Auto-detect context limit from known providers
        if ctx_limit is None:
            for domain, limit in self._KNOWN_LIMITS.items():
                if domain in base_url:
                    ctx_limit = limit
                    break

        entry = {"base_url": base_url, "model": model}
        if api_key:
            entry["api_key"] = api_key
        if ctx_limit:
            entry["context_limit"] = ctx_limit

        self._models[name] = entry
        self._save_models()

        # Test connection
        client = LLMClient(base_url=base_url, model=model, api_key=api_key)
        ok, msg = client.check_connection()
        status = "connected" if ok else f"warning: {msg}"
        print(f"  Added: {name} ({base_url} / {model}) — {status}")

    def _model_remove(self, name: str):
        if not name:
            print("  Usage: /model remove <name>")
            return
        if name == "local":
            print("  Cannot remove 'local' model")
            return
        if name not in self._models:
            print(f"  {t('unknown_model')}: {name}")
            return
        if name == self.active_model:
            self._switch_model("local")
        del self._models[name]
        self._save_models()
        print(f"  Removed: {name}")
