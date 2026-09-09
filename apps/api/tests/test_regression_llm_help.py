from regression_support import *  # noqa: F401,F403

class RegressionTests(unittest.TestCase):
    def test_extended_llm_provider_catalog_is_configured(self):
        from llm import _DEFAULT_MODELS, _ENV_NAMES, _KEY_NAMES, _OPENAI_COMPAT_BASE_URLS

        providers = {
            "xai", "kimi", "mistral", "openrouter", "together", "fireworks",
            "cerebras", "perplexity", "huggingface", "cohere", "sambanova",
            "qwen", "azure", "custom",
        }
        for provider in providers:
            self.assertIn(provider, _KEY_NAMES)
            self.assertIn(provider, _ENV_NAMES)
            self.assertIn(provider, _DEFAULT_MODELS)
        for provider in providers - {"azure", "custom"}:
            self.assertTrue(_OPENAI_COMPAT_BASE_URLS[provider].startswith("https://"))

    def test_azure_provider_without_endpoint_falls_back_cleanly(self):
        from pydantic import BaseModel
        from data.repository import create_repository
        from llm import call_llm, configure_repository

        class Payload(BaseModel):
            value: str = ""

        class Settings:
            def get_setting(self, key, default=""):
                return {
                    "llm_provider": "azure",
                    "azure_openai_api_key": "fake-key",
                    "azure_model": "deployment-name",
                }.get(key, default)

        class Repo:
            settings = Settings()

        try:
            configure_repository(Repo())
            result = call_llm("system", "user", Payload)
            self.assertEqual(result.value, "")
        finally:
            configure_repository(create_repository())

    def test_model_facing_agents_have_production_guardrails(self):
        """Every prompt fed untrusted text must carry injection guardrails.

        Narrowed when JustHireMe's scraper and browser actuator were removed: the scout extraction
        prompts and the vision actuator's `_VISION_SYSTEM` went with them, and the
        "do not click final" assertion retired with the actuator — nothing in this app clicks
        anything now. The equivalent guarantee for autofill lives in the extension's own guard
        (`apps/extension/src/fill/guard.ts`), which has its own tests.

        Keep this list current: any new prompt that sees job-posting or email text belongs here.
        """
        import inspect
        from ranking import evaluator
        from generation import generator

        contracts = [
            evaluator._SYSTEM_PROMPT,
            inspect.getsource(generator._draft_package),
        ]
        joined = "\n".join(contracts).lower()

        self.assertIn("production", joined)
        self.assertIn("untrusted", joined)
        self.assertIn("never invent", joined)
