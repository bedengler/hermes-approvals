"""Integration seam for a Hermes dashboard host.

The host must inject its canonical auth dependency and a profile-resolved
ApprovalStore. This module intentionally does not read credentials or bypass
the dashboard middleware.
"""
from hermes_approval.api import create_router
from hermes_approval.governance import GovernanceApprovalStore, GovernanceDecisionService


def build_router(store, dashboard_authorize, governance_path=None, governance_decide=None):
    governance = GovernanceApprovalStore(governance_path) if governance_path else None
    decision_service = None
    if governance and governance_decide:
        decision_service = GovernanceDecisionService(governance, governance_decide)
    return create_router(store, authorize=dashboard_authorize, governance_store=governance,
                         governance_decision_service=decision_service)
