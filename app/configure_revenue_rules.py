"""Idempotently configure the initial approved revenue rules."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import TextIO

from app.security.auth import AuthService, Session
from app.security.user_manager import AuthorizationError
from app.services.revenue_rule_service import RevenueRuleService


def _effective_from(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("effective_from must use ISO YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError("effective_from must use ISO YYYY-MM-DD")
    return value


def configure(auth: AuthService, *, username: str, effective_from: str,
              dry_run: bool = False, output: TextIO = sys.stdout) -> dict[str, int]:
    """Validate and atomically apply the initial configuration plan."""
    effective_from = _effective_from(effective_from)
    service = RevenueRuleService(auth)
    connection = auth.connect()
    summary = {"created": 0, "already_correct": 0, "conflicts": 0,
               "michigan": 0, "north_carolina": 0, "unconfigured": 0}
    try:
        connection.execute("BEGIN")
        user = connection.execute("SELECT id,username,display_name,role,is_active FROM Users "
            "WHERE username=? COLLATE NOCASE", (username.strip(),)).fetchone()
        if user is None or not user["is_active"] or user["role"] != "admin":
            raise AuthorizationError("An active administrator username is required")
        session = Session(user["id"], user["username"], user["role"], user["display_name"])

        douglas = connection.execute("""SELECT tech_id FROM Techs
            WHERE trim(first_name)=? COLLATE NOCASE AND trim(last_name)=? COLLATE NOCASE
            ORDER BY tech_id""", ("Douglas", "Willett")).fetchall()
        if not douglas:
            print("Missing Douglas Willett technician record; no changes made.", file=output)
            raise LookupError("No exact Douglas Willett technician match")
        if len(douglas) > 1:
            print(f"Duplicate Douglas Willett matches ({len(douglas)}); no changes made.", file=output)
            raise ValueError("Multiple exact Douglas Willett technician matches")
        tech_id = int(douglas[0][0])
        print(f"Resolved Douglas Willett tech_id={tech_id}.", file=output)

        candidates: list[tuple[str, dict]] = [
            ("technician", {"scope_type": "System", "scope_id": None,
             "rule_type": "Percentage", "rule_value": 7000,
             "compensation_component": "Overall", "effective_from": effective_from}),
            ("technician", {"scope_type": "Technician", "scope_id": tech_id,
             "rule_type": "Percentage", "rule_value": 0,
             "compensation_component": "Overall", "effective_from": effective_from}),
        ]
        has_state = "state" in {row[1] for row in connection.execute("PRAGMA table_info(Markets)")}
        state_expression = "state" if has_state else "NULL AS state"
        markets = connection.execute(f"SELECT market_id,market_name,{state_expression} FROM Markets "
                                     "ORDER BY state,market_name,market_id").fetchall()
        if not has_state:
            print("Markets.state is unavailable; all markets require a revenue-share decision.",
                  file=output)
        for market in markets:
            state = (market["state"] or "").strip().upper()
            if state in {"MI", "MICHIGAN"}:
                share = 0; summary["michigan"] += 1
            elif state in {"NC", "NORTH CAROLINA"}:
                share = 1000; summary["north_carolina"] += 1
            else:
                summary["unconfigured"] += 1
                print(f"UNCONFIGURED market_id={market['market_id']} "
                      f"{market['market_name']} ({market['state'] or 'no state'})", file=output)
                continue
            candidates.append(("market", {"market_id": int(market["market_id"]),
                "recipient_code": "LUNATECH_EAST", "share_basis_points": share,
                "effective_from": effective_from,
                "notes": "Initial approved revenue-share configuration"}))

        planned: list[tuple[str, dict]] = []
        for kind, candidate in candidates:
            if kind == "technician":
                rows = connection.execute("""SELECT * FROM TechnicianCompensationRules
                    WHERE scope_type=? AND scope_id IS ? AND compensation_component=? AND is_active=1
                      AND (? IS NULL OR effective_from IS NULL OR effective_from<=?)
                      AND (effective_to IS NULL OR effective_to>=?)""",
                    (candidate["scope_type"], candidate["scope_id"],
                     candidate["compensation_component"], None, effective_from,
                     effective_from)).fetchall()
                identical = [row for row in rows if row["rule_type"] == candidate["rule_type"]
                    and row["rule_value"] == candidate["rule_value"]
                    and row["effective_from"] == effective_from and row["effective_to"] is None]
                label = f"{candidate['scope_type']}:{candidate['scope_id']} Overall"
            else:
                rows = connection.execute("""SELECT * FROM MarketRevenueShareRules
                    WHERE market_id=? AND recipient_code=? AND is_active=1
                      AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)""",
                    (candidate["market_id"], candidate["recipient_code"], effective_from,
                     effective_from)).fetchall()
                identical = [row for row in rows if row["share_basis_points"] ==
                    candidate["share_basis_points"] and row["effective_from"] == effective_from
                    and row["effective_to"] is None]
                label = f"Market:{candidate['market_id']} {candidate['recipient_code']}"
            if len(rows) == 1 and len(identical) == 1:
                summary["already_correct"] += 1
                print(f"ALREADY CORRECT {label}", file=output)
            elif rows:
                summary["conflicts"] += 1
                print(f"CONFLICT {label}: existing rule(s) overlap {effective_from}", file=output)
            else:
                planned.append((kind, candidate))
                print(f"WOULD CREATE {label}", file=output)

        if summary["conflicts"]:
            raise ValueError("Conflicting existing revenue rules; no changes made")
        if not dry_run:
            for kind, candidate in planned:
                if kind == "technician":
                    service._create_technician(connection, session, candidate)
                else:
                    service._create_market(connection, session, candidate)
                summary["created"] += 1
            connection.commit()
            print(f"Applied {summary['created']} rule(s).", file=output)
        else:
            connection.rollback()
            print(f"Dry run: {len(planned)} rule(s) would be created; no changes made.", file=output)
        return summary
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure initial Matterport Ops revenue rules")
    parser.add_argument("--username", required=True,
                        help="active administrator responsible for this configuration")
    parser.add_argument("--effective-from", required=True, help="ISO YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        configure(AuthService(), username=args.username, effective_from=args.effective_from,
                  dry_run=args.dry_run)
    except (AuthorizationError, LookupError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
