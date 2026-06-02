"""
services/copilot/handlers/navigation.py — Navigation & Info Handlers
"""


def navigate(workspace_id: int, user_id: int, url: str, **_) -> dict:
    """Return URL for frontend to navigate to."""
    ALLOWED_PREFIXES = ['/', '/campaign', '/inbox', '/contacts', '/settings',
                        '/analytics', '/deliverability', '/automations', '/admin']
    if not any(url.startswith(p) for p in ALLOWED_PREFIXES):
        raise ValueError('Invalid navigation URL')
    return {'message': f'Navigating to {url}', 'url': url}


def show_info(workspace_id: int, user_id: int, info: str, **_) -> dict:
    """Display additional info — just passes through."""
    return {'message': info}
