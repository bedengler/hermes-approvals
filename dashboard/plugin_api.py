"""Integration seam for a Hermes dashboard host.

The host must inject its canonical auth dependency and a profile-resolved
ApprovalStore. This module intentionally does not read credentials or bypass
the dashboard middleware.
"""
from hermes_approval.api import create_router


def build_router(store, dashboard_authorize):
    return create_router(store, authorize=dashboard_authorize)
