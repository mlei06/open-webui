"""Fail closed before provisioning plain internal case data (secret-free errors)."""
from davy_connection import ApiError, base_model_of, call, norm

DAVY_GEMMA = 'gemma-4-31b-it'


def validate_env(env, base_model=None):
    """The service currently supports plain names only; aliases are not implemented."""
    if (env.get('QDTS_CUSTOMER_NAMES') or 'plain').strip().lower() != 'plain':
        raise ApiError('QDTS_CUSTOMER_NAMES must be plain: this case service has no alias mode; do not use it with outside models')
    if (env.get('XAI_API_KEY') or '').strip():
        raise ApiError('Plain QDTS customer names refused while XAI_API_KEY is configured; remove the outside connection before provisioning')
    if (base_model or base_model_of(env)) != DAVY_GEMMA:
        raise ApiError('Plain QDTS customer names require the Davy Gemma preset base model (gemma-4-31b-it)')
    urls = [norm(u) for u in env.get('OPENAI_API_BASE_URLS', '').split(';') if u.strip()]
    if len(urls) > 1:
        raise ApiError('Plain QDTS customer names require a single approved Davy connection, not multiple model connections')


def validate_live(base, token, env):
    """Saved connections survive removing an env key. Do not silently delete them."""
    approved = [norm(u) for u in env.get('OPENAI_API_BASE_URLS', '').split(';') if u.strip()]
    if len(approved) != 1:
        raise ApiError('Set OPENAI_API_BASE_URLS to the single approved Davy endpoint before provisioning cases')
    cfg = call(base, 'GET', '/openai/config', token) or {}
    urls = cfg.get('OPENAI_API_BASE_URLS', [])
    keys = cfg.get('OPENAI_API_KEYS', [])
    configs = cfg.get('OPENAI_API_CONFIGS', {})
    # Preserve URL indexes: dropping blank URLs or padding keys could associate a
    # disabled flag/key with the wrong connection. Configs may be sparse; an
    # omitted index defaults to enabled, just as it does in Open WebUI.
    if (not isinstance(urls, list) or not isinstance(keys, list)
            or len(urls) != len(keys) or not isinstance(configs, dict)
            or any(not isinstance(u, str) or not u.strip() for u in urls)
            or any(not isinstance(k, str) for k in keys)
            or any(i not in {str(n) for n in range(len(urls))}
                   or not isinstance(c, dict) for i, c in configs.items())):
        raise ApiError('Plain QDTS customer names refused: saved model connection URLs, keys and configs cannot be aligned')
    saved = [norm(u) for u in urls]
    if approved[0] not in saved:
        raise ApiError('Plain QDTS customer names refused: the approved Davy endpoint is missing from saved model connections')
    if any(u != approved[0] and keys[i].strip()
           and configs.get(str(i), {}).get('enable') is not False
           for i, u in enumerate(saved)):
        raise ApiError('Plain QDTS customer names refused: an enabled keyed saved non-Davy model connection exists; an admin must disable it or remove its API key first')
