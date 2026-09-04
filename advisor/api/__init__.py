"""Advisor REST API & Microservice Package.

Provides high-level REST endpoints (/api/v1/...) and decoupled ApiClient SDK
for airline operations control consoles, web dashboards, and mobile clients.
"""

from advisor.api.client import ApiClient, get_api_client

__all__ = ["ApiClient", "get_api_client"]
