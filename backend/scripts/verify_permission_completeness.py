"""Standalone completeness verification runner for the M.A.N.T.A. Permission Matrix and API Routes (SCRUM-310).

Parent User Story: SCRUM-247 (US-11 Role-based access control)

Objective:
Fail with exit code 1 if a role/action pair is missing from the matrix or a route has no permission.

Usage:
    python scripts/verify_permission_completeness.py [--target {all,matrix,routes}] [--verbose]
"""

import argparse
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.auth.permissions import (  # noqa: E402
    extract_all_app_routes,
    get_permission_matrix,
    validate_permission_matrix_completeness,
    validate_registered_routes_completeness,
)
from app.main import app  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify completeness of M.A.N.T.A. permission matrix and route permissions (SCRUM-310)."
    )
    parser.add_argument(
        "--target",
        choices=["all", "matrix", "routes"],
        default="all",
        help="Validation target (matrix, routes, or all; default: all)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print detailed inspection logs",
    )
    return parser.parse_args(argv)


def run_checks(target: str = "all", verbose: bool = False) -> int:
    exit_code = 0
    matrix = get_permission_matrix()

    print("=" * 70)
    print("M.A.N.T.A. Permission Completeness Verification (SCRUM-310 / US-11)")
    print("=" * 70)

    # 1. Matrix Completeness Check
    if target in ("all", "matrix"):
        print("\n[1/2] Verifying Permission Matrix Completeness...")
        print(f"      Matrix path: {matrix.matrix_path}")
        matrix_errors = validate_permission_matrix_completeness()
        total_roles = len(matrix.roles_data)
        total_actions = len(matrix.actions_data)
        expected_cells = total_roles * total_actions

        if verbose:
            print(
                f"      Declared roles:   {total_roles} ({', '.join(sorted(matrix.roles_data.keys()))})"
            )
            print(f"      Declared actions: {total_actions}")
            print(f"      Expected cells:   {expected_cells}")

        if matrix_errors:
            print(f"  [FAIL] Matrix has {len(matrix_errors)} completeness violation(s):")
            for err in matrix_errors:
                print(f"         - {err}")
            exit_code = 1
        else:
            print(
                f"  [PASS] All {total_roles} roles x {total_actions} actions ({expected_cells} cells) have explicit decisions."
            )

    # 2. Registered Route Completeness Check
    if target in ("all", "routes"):
        print("\n[2/2] Verifying Registered API Route Permissions...")
        route_errors = validate_registered_routes_completeness(app, matrix=matrix)
        all_routes = extract_all_app_routes(app)

        if verbose:
            print(f"      Total endpoints inspected: {len(all_routes)}")

        if route_errors:
            print(f"  [FAIL] Found {len(route_errors)} route permission violation(s):")
            for err in route_errors:
                print(f"         - {err}")
            exit_code = 1
        else:
            print(
                "  [PASS] Every registered non-public API route has a declared, valid permission."
            )

    print("\n" + "=" * 70)
    if exit_code == 0:
        print("RESULT: ALL COMPLETENESS CHECKS PASSED.")
    else:
        print("RESULT: COMPLETENESS CHECKS FAILED. Build must be broken.")
    print("=" * 70)

    return exit_code


def main() -> None:
    args = parse_args()
    code = run_checks(target=args.target, verbose=args.verbose)
    sys.exit(code)


if __name__ == "__main__":
    main()
