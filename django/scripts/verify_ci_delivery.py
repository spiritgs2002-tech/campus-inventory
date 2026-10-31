#!/usr/bin/env python3
"""Read-only, stdlib-only CI delivery guard, usable before dependency setup."""

import argparse
import hashlib
import json
from pathlib import Path

LAYOUTS = (Path("."), Path("inventory-v4/django"), Path("django"))
PHASES = (
    "verify_p1",
    "verify_p2_p3",
    "verify_p4",
    "verify_p5",
    "verify_p6",
    "verify_p7",
    "verify_readiness",
)


def application_root(repo_root):
    for relative in LAYOUTS:
        candidate = repo_root / relative
        if (candidate / "manage.py").is_file():
            return candidate.resolve()
    raise ValueError("CI_APPLICATION_MISSING: no supported manage.py layout")


def inspect_delivery(repo_root):
    repo_root = Path(repo_root).resolve()
    workflow = repo_root / ".github/workflows/ci.yml"
    if not workflow.is_file():
        raise ValueError(f"CI_WORKFLOW_MISSING: expected {workflow}")
    app = application_root(repo_root)
    template = app / "docs/ci/ci.yml.template"
    if not template.is_file() or workflow.read_bytes() != template.read_bytes():
        raise ValueError(
            "CI_TEMPLATE_DRIFT: canonical workflow and portable copy differ"
        )
    for base in (app, app.parent):
        folder = base / ".github/workflows"
        if not base.is_relative_to(repo_root) or folder == workflow.parent:
            continue
        for name in ("ci.yml", "inventory-p1.yml"):
            if (folder / name).is_file():
                raise ValueError(f"CI_DUPLICATE_WORKFLOW: {folder / name}")
    body = workflow.read_text()
    for phase in PHASES:
        if f"scripts/{phase}.py" not in body:
            raise ValueError(f"CI_PHASE_MISSING: {phase}")
    if "manage.py test tests --no-input" not in body:
        raise ValueError(
            "CI_TEST_SUITE_MISSING: explicit Django regression step required"
        )
    return {
        "repository_root": str(repo_root),
        "application_root": str(app),
        "workflow": str(workflow),
        "workflow_sha256": hashlib.sha256(workflow.read_bytes()).hexdigest(),
        "portable_copy": str(template),
        "template_matches": True,
        "git_checkout_present": (repo_root / ".git").exists(),
        "phase_scripts": list(PHASES),
        "explicit_test_suite": True,
        "remote_actions_verified": False,
    }


def infer_root():
    app = Path(__file__).resolve().parents[1]
    roots = []
    for candidate in (app, *app.parents):
        if (candidate / ".github/workflows/ci.yml").is_file():
            try:
                if application_root(candidate) == app:
                    roots.append(candidate)
            except ValueError:
                continue
    if len(roots) != 1:
        raise ValueError("CI_ROOT_AMBIGUOUS: specify --repo-root explicitly")
    return roots[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args()
    try:
        result = inspect_delivery(args.repo_root or infer_root())
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
