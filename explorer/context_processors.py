from django.conf import settings

from . import ai


def app_settings(request):
    """What every page needs: whether the history is on, and the AI settings
    shown in the Settings dialog (never the server's key itself)."""
    config = ai.server_config()
    server = None if config is None else {
        'provider': config.provider,
        'label': config.label,
        'model': config.effective_model,
    }
    return {
        'history_enabled': settings.HISTORY_ENABLED,
        'fusion_config': {
            'historyEnabled': settings.HISTORY_ENABLED,
            'aiServer': server,
            'allowBrowserKeys': settings.AI_ALLOW_BROWSER_KEYS,
            'allowCustomBaseUrl': settings.AI_ALLOW_CUSTOM_BASE_URL,
            'providers': ai.PROVIDERS,
            'defaultModels': {
                'gemini': settings.GEMINI_MODEL,
                'anthropic': settings.ANTHROPIC_MODEL,
                'openai': settings.OPENAI_MODEL,
            },
            'defaultBaseUrl': settings.OPENAI_BASE_URL,
        },
    }
