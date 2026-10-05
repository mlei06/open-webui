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
    saved = [norm(u) for u in cfg.get('OPENAI_API_BASE_URLS') or [] if u.strip()]
    if any(u != approved[0] for u in saved):
        raise ApiError('Plain QDTS customer names refused: a saved non-Davy model connection exists; an admin must remove it first')
