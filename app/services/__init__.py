"""Application service layer."""

from app.services.technician_service import TechnicianService
from app.services.payment_service import PaymentService

# Expected job revenue is an operational Jobs value, separate from technician
# compensation and received Matterport revenue. Extend the existing JobsService
# field/validation contract here so callers importing app.services.jobs_service
# receive the same behavior without duplicating the service implementation.
from app.services import jobs_service as _jobs_service

_jobs_service._JOB_FIELDS = frozenset({*_jobs_service._JOB_FIELDS, "expected_job_revenue"})
_original_clean_job = _jobs_service.JobsService._clean_job.__func__


def _clean_job_with_expected_revenue(cls, data, *, creating):
    data = dict(data)
    has_expected_revenue = "expected_job_revenue" in data
    expected_revenue = data.pop("expected_job_revenue", None)
    clean = _original_clean_job(cls, data, creating=creating)
    if has_expected_revenue:
        clean["expected_job_revenue"] = cls._clean_number(
            "expected_job_revenue", expected_revenue
        )
    return clean


_jobs_service.JobsService._clean_job = classmethod(_clean_job_with_expected_revenue)

__all__ = ["PaymentService", "TechnicianService"]
