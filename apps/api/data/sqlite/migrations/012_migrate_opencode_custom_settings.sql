-- OpenCode Go used to be configured through the generic custom endpoint UI.
-- Move only the official Go endpoint to the dedicated provider so runtime calls
-- receive OpenCode's required routing headers.  Keep the custom values intact:
-- they remain a recoverable copy and may still be useful if the user switches
-- back to the compatibility provider later.

INSERT OR IGNORE INTO settings(key, val)
SELECT 'opencode_api_key', val
FROM settings
WHERE key = 'custom_api_key'
  AND trim(val) <> ''
  AND lower(rtrim((SELECT val FROM settings WHERE key = 'custom_base_url'), '/')) IN (
    'https://opencode.ai/zen/go/v1',
    'https://opencode.ai/zen/go/v1/responses',
    'https://opencode.ai/zen/go/v1/chat/completions',
    'https://opencode.ai/zen/go/v1/messages'
  );

INSERT OR IGNORE INTO settings(key, val)
SELECT 'opencode_base_url', val
FROM settings
WHERE key = 'custom_base_url'
  AND lower(rtrim(val, '/')) IN (
    'https://opencode.ai/zen/go/v1',
    'https://opencode.ai/zen/go/v1/responses',
    'https://opencode.ai/zen/go/v1/chat/completions',
    'https://opencode.ai/zen/go/v1/messages'
  );

INSERT OR IGNORE INTO settings(key, val)
SELECT 'opencode_model', val
FROM settings
WHERE key = 'custom_model'
  AND trim(val) <> ''
  AND lower(rtrim((SELECT val FROM settings WHERE key = 'custom_base_url'), '/')) IN (
    'https://opencode.ai/zen/go/v1',
    'https://opencode.ai/zen/go/v1/responses',
    'https://opencode.ai/zen/go/v1/chat/completions',
    'https://opencode.ai/zen/go/v1/messages'
  );

INSERT OR IGNORE INTO settings(key, val)
SELECT 'opencode_protocol', val
FROM settings
WHERE key = 'custom_protocol'
  AND trim(val) <> ''
  AND lower(rtrim((SELECT val FROM settings WHERE key = 'custom_base_url'), '/')) IN (
    'https://opencode.ai/zen/go/v1',
    'https://opencode.ai/zen/go/v1/responses',
    'https://opencode.ai/zen/go/v1/chat/completions',
    'https://opencode.ai/zen/go/v1/messages'
  );

INSERT OR IGNORE INTO settings(key, val)
SELECT 'opencode_endpoint_type', val
FROM settings
WHERE key = 'custom_endpoint_type'
  AND trim(val) <> ''
  AND lower(rtrim((SELECT val FROM settings WHERE key = 'custom_base_url'), '/')) IN (
    'https://opencode.ai/zen/go/v1',
    'https://opencode.ai/zen/go/v1/responses',
    'https://opencode.ai/zen/go/v1/chat/completions',
    'https://opencode.ai/zen/go/v1/messages'
  );

INSERT OR IGNORE INTO settings(key, val)
SELECT 'opencode_reasoning_effort', val
FROM settings
WHERE key = 'custom_reasoning_effort'
  AND trim(val) <> ''
  AND lower(rtrim((SELECT val FROM settings WHERE key = 'custom_base_url'), '/')) IN (
    'https://opencode.ai/zen/go/v1',
    'https://opencode.ai/zen/go/v1/responses',
    'https://opencode.ai/zen/go/v1/chat/completions',
    'https://opencode.ai/zen/go/v1/messages'
  );

UPDATE settings
SET val = 'opencode'
WHERE key IN (
    'llm_provider',
    'scout_provider',
    'evaluator_provider',
    'generator_provider',
    'ingestor_provider',
    'actuator_provider'
  )
  AND val = 'custom'
  AND lower(rtrim((SELECT val FROM settings WHERE key = 'custom_base_url'), '/')) IN (
    'https://opencode.ai/zen/go/v1',
    'https://opencode.ai/zen/go/v1/responses',
    'https://opencode.ai/zen/go/v1/chat/completions',
    'https://opencode.ai/zen/go/v1/messages'
  );
