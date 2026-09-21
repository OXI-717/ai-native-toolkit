from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BudgetGuard:
    command: str
    project: str
    provider: str
    provider_mode: str
    allow_paid: bool
    planned_calls: int
    per_run_budget_usd: float | None
    monthly_budget_usd: float | None = None
    estimated_cost_usd: float | None = None
    cache_hits: int = 0
    price_table_version: str = "unknown"

    actual_calls: int = 0
    provider_cost_usd: float = 0.0
    stopped_for_budget: bool = False
    _last_observed_call_cost: float | None = None

    def preflight(self) -> dict[str, Any] | None:
        if self.provider_mode != "live":
            return self._error(
                "PAID_PROVIDER_MODE_NOT_LIVE",
                "Paid provider calls require provider_mode live.",
                {"provider_mode": self.provider_mode},
            )
        if not self.allow_paid:
            return self._error(
                "PAID_CALL_NOT_CONFIRMED",
                "Paid provider calls require allow_paid.",
                {"allow_paid": False},
            )
        if self.per_run_budget_usd is None:
            return self._error(
                "PAID_BUDGET_NOT_CONFIGURED",
                "Paid provider calls require a configured per-run budget.",
                {},
            )
        if self.estimated_cost_usd is not None and self.estimated_cost_usd > self.per_run_budget_usd:
            return self._error(
                "PAID_BUDGET_PRECHECK_EXCEEDED",
                "Estimated provider cost exceeds the configured per-run budget.",
                {
                    "estimated_cost_usd": self.estimated_cost_usd,
                    "budget_limit_usd": self.per_run_budget_usd,
                },
            )
        projected_next_cost = self._last_observed_call_cost
        if (
            projected_next_cost is not None
            and self.provider_cost_usd + projected_next_cost > self.per_run_budget_usd
        ):
            self.stopped_for_budget = True
            return self._error(
                "PAID_BUDGET_RUNTIME_EXCEEDED",
                "Observed provider cost reached the configured per-run budget.",
                {
                    "provider_cost_usd": self.provider_cost_usd,
                    "projected_next_call_cost_usd": projected_next_cost,
                    "budget_limit_usd": self.per_run_budget_usd,
                },
            )
        return None

    def record_provider_cost(self, cost_usd: float) -> dict[str, Any] | None:
        self.actual_calls += 1
        self.provider_cost_usd = round(self.provider_cost_usd + max(float(cost_usd), 0.0), 6)
        self._last_observed_call_cost = max(float(cost_usd), 0.0)
        if self.per_run_budget_usd is not None and self.provider_cost_usd > self.per_run_budget_usd:
            self.stopped_for_budget = True
            return self._error(
                "PAID_BUDGET_RUNTIME_EXCEEDED",
                "Observed provider cost exceeds the configured per-run budget.",
                {
                    "provider_cost_usd": self.provider_cost_usd,
                    "budget_limit_usd": self.per_run_budget_usd,
                },
            )
        return None

    def manifest(self) -> dict[str, Any]:
        return {
            "provider_mode": self.provider_mode,
            "allow_paid": self.allow_paid,
            "planned_calls": self.planned_calls,
            "actual_calls": self.actual_calls,
            "cache_hits": self.cache_hits,
            "estimated_cost_usd": self.estimated_cost_usd,
            "provider_cost_usd": self.provider_cost_usd,
            "budget_limit_usd": self.per_run_budget_usd,
            "monthly_budget_usd": self.monthly_budget_usd,
            "price_table_version": self.price_table_version,
            "stopped_for_budget": self.stopped_for_budget,
        }

    def _error(self, code: str, safe_message: str, details: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "code": code,
            "provider": self.provider,
            "endpoint": None,
            "safe_message": safe_message,
            "retryable": False,
            "details": {
                "command": self.command,
                "project": self.project,
                "planned_calls": self.planned_calls,
                **details,
            },
        }
