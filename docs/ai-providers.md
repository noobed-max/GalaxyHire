# AI provider configuration

GalaxyHire lets you choose the provider used for résumé parsing, matching, suggestions, and document generation. Open **Settings**, choose a provider, enter its credentials and model, then use **Check API** before parsing a résumé.

Provider settings are kept separate. The **OpenAI-compatible** and **OpenCode** panels have independent endpoint, key, model, protocol, request-format, and reasoning settings.

## OpenAI-compatible endpoints

Choose **OpenAI-compatible** for an endpoint that implements the OpenAI Chat Completions or Responses shape.

- Base URL: the provider's `/v1` base, for example `https://api.example.com/v1`
- Model: the exact model identifier accepted by that endpoint
- Protocol: OpenAI or Anthropic when the endpoint explicitly supports both
- Request format: Chat or Responses for OpenAI protocol endpoints
- Reasoning: None, Low, Medium, or High when the model supports it

Do not paste `/chat/completions` or `/responses` into a base URL unless you intentionally configure the matching request format; the app normalizes full endpoint URLs as well.

## Anthropic

Choose **Anthropic** for the native Claude API. Enter the API key and model shown in your Anthropic account. The native Messages format and Claude thinking settings are selected automatically by the Anthropic panel.

## OpenCode Go

Choose **OpenCode** as its own provider. Do not configure OpenCode Go as a generic custom endpoint.

Recommended defaults:

| Setting | Value |
|---|---|
| Endpoint | `https://opencode.ai/zen/go/v1` |
| Protocol | OpenAI |
| Request format | Responses |
| Model | `muse-spark-1.3-contributor` |
| Reasoning | Medium |

Enter your OpenCode Go API key in the OpenCode panel. GalaxyHire sends the stable `x-opencode-session` routing header on every OpenCode request. If you see an error saying that the header is missing, verify that the provider is **OpenCode**, not **OpenAI-compatible**, and restart the app after updating an older installation.

## Ollama

Ollama runs locally and does not require an API key. Start Ollama, make sure its local endpoint is reachable, and choose the model you have pulled. Local providers can be useful for keeping résumé text on your machine, but model quality and context limits vary.

## Subscription CLI providers

Claude CLI, Codex CLI, Gemini CLI, and Copilot CLI use the account already logged into the corresponding command-line tool. They do not use an API key in GalaxyHire. Install and log into the CLI separately, then select its provider in Settings.

## Provider safety

- Never commit an API key, paste one into an issue, or share a screenshot containing it.
- Use **Check API** to validate the current settings before a long résumé parse.
- Keep the model name exactly as the provider documents it.
- Use a provider you trust with résumé, job, and application data.
- If a provider rejects one request shape, use its documented endpoint format; OpenAI compatibility does not guarantee image, PDF, or reasoning support.
