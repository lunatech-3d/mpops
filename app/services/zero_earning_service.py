"""Resolve approved zero-dollar technician earnings without creating a bank payment."""

from __future__ import annotations

from typing import Any

from app.date_utils import utc_now_iso
from app.security.audit import record_event
from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError


class ZeroEarningService:
    """Mark legitimate $0 technician earnings complete with an audit trail."""

    def __init__(self, auth: AuthService):
        self.auth = auth

    @staticmethod
    def _require_operator(session: Session | None) -> None:
        if session is None or session.role not in {"admin", "operator"}:
            raise AuthorizationError("Administrator or operator role required")

    @staticmethod
    def _earning_id(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("earning_id must be a positive integer")
        return value

    def settle_zero_earnings(
        self,
        session: Session,
        earning_ids: list[int],
        *,
        reason: str = "Zero-dollar internal technician earning; no payment required",
    ) -> dict[str, Any]:
        """Resolve approved zero-dollar earnings without a TechnicianPayments row.

        This is intentionally not a payment. It records that the earning was reviewed,
        nothing is owed, and no external funds were issued.
        """
        self._require_operator(session)
        if not isinstance(earning_ids, list) or not earning_ids:
            raise ValueError("Select at least one zero-dollar earning")
        ids = [self._earning_id(value) for value in earning_ids]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate earning selected")
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("reason is required")
        if len(reason) > 1000:
            raise ValueError("reason may not exceed 1000 characters")

        now = utc_now_iso()
        settled = []
        with self.auth.connection() as connection:
            for earning_id in ids:
                row = connection.execute(
                    "SELECT technician_earning_id, tech_id, job_id, payment_batch_id, "
                    "earning_status, net_earning_cents, voided_at "
                    "FROM TechnicianJobEarnings WHERE technician_earning_id=?",
                    (earning_id,),
                ).fetchone()
                if row is None:
                    raise LookupError(f"Earning {earning_id} not found")
                if row["voided_at"] is not None:
                    raise ValueError(f"Earning {earning_id} is voided")
                if row["earning_status"] not in {"Approved", "Paid"}:
                    raise ValueError(f"Earning {earning_id} is not approved")
                if int(row["net_earning_cents"] or 0) != 0:
                    raise ValueError(f"Earning {earning_id} is not a zero-dollar earning")

                connection.execute(
                    "UPDATE TechnicianJobEarnings SET earning_status='Paid', paid_at=? "
                    "WHERE technician_earning_id=?",
                    (now, earning_id),
                )
                record_event(
                    connection,
                    "zero_dollar_technician_earning_resolved",
                    actor_user_id=session.user_id,
                    details={
                        "technician_earning_id": earning_id,
                        "technician_id": row["tech_id"],
                        "job_id": row["job_id"],
                        "payment_batch_id": row["payment_batch_id"],
                        "amount_cents": 0,
                        "external_payment_issued": False,
                        "reason": reason,
                    },
                )
                settled.append(earning_id)

        return {"settled_count": len(settled), "earning_ids": settled, "amount_cents": 0}
