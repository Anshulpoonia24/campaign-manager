"""
services/copilot/handlers/navigation.py — Navigation & Info Handlers
"""


def navigate(workspace_id: int, user_id: int, url: str = '/', **_) -> dict:
    """Return URL for frontend to navigate to."""
    ALLOWED_PREFIXES = ['/', '/campaign', '/inbox', '/contacts', '/settings',
                        '/analytics', '/deliverability', '/automations', '/admin']
    if not any(url.startswith(p) for p in ALLOWED_PREFIXES):
        raise ValueError('Invalid navigation URL')
    return {'message': f'Navigating to {url}', 'url': url}


def show_info(workspace_id: int, user_id: int, info: str = '', **_) -> dict:
    """Display additional info — just passes through."""
    return {'message': info or 'No additional info.'}


def create_campaign(workspace_id: int, user_id: int, **_) -> dict:
    """Navigate to campaign creation."""
    return {'message': 'Opening campaign creator...', 'url': '/campaigns/new'}


def add_smtp(workspace_id: int, user_id: int, **_) -> dict:
    """Navigate to SMTP setup."""
    return {'message': 'Opening SMTP settings...', 'url': '/settings'}


def upload_contacts(workspace_id: int, user_id: int, **_) -> dict:
    """Navigate to contacts upload."""
    return {'message': 'Opening contacts page...', 'url': '/contacts'}


def view_analytics(workspace_id: int, user_id: int, **_) -> dict:
    """Navigate to analytics."""
    return {'message': 'Opening analytics...', 'url': '/analytics'}


def open_settings(workspace_id: int, user_id: int, **_) -> dict:
    """Navigate to settings."""
    return {'message': 'Opening settings...', 'url': '/settings'}
