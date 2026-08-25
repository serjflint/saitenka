"""Negative controls for the application package-layout gate."""

from __future__ import annotations

import app_package_layout


def _layout(tmp_path):
    app = tmp_path / "src" / "saitenka" / "app"
    for name in app_package_layout.FEATURE_PACKAGES:
        package = app / "features" / name
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    return app


def test_current_package_layout_is_declared() -> None:
    assert app_package_layout.inspect_tree() == []


def test_retired_flat_module_and_import_are_rejected(tmp_path) -> None:
    app = _layout(tmp_path)
    (app / "miner.py").write_text("", encoding="utf-8")
    consumer = app / "consumer.py"
    consumer.write_text("from saitenka.app.miner import mine\n", encoding="utf-8")

    findings = app_package_layout.inspect_tree(app)

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("retired-flat-import", "saitenka.app.miner"),
        ("retired-flat-module", "miner"),
    ]


def test_retired_from_app_import_is_rejected(tmp_path) -> None:
    app = _layout(tmp_path)
    (app / "consumer.py").write_text("from saitenka.app import miner\n", encoding="utf-8")

    findings = app_package_layout.inspect_tree(app)

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("retired-flat-import", "saitenka.app.miner")
    ]


def test_retired_dynamic_target_is_rejected(tmp_path) -> None:
    app = _layout(tmp_path)
    (app / "consumer.py").write_text("TARGET = 'saitenka.app.miner.bulk_mine'\n", encoding="utf-8")

    findings = app_package_layout.inspect_tree(app)

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("retired-flat-import", "saitenka.app.miner")
    ]


def test_package_inventory_rejects_missing_and_undeclared_features(tmp_path) -> None:
    app = _layout(tmp_path)
    (app / "features" / "help" / "__init__.py").unlink()
    (app / "features" / "help").rmdir()
    (app / "features" / "unbounded").mkdir()

    findings = app_package_layout.inspect_tree(app)

    assert [(finding.rule, finding.detail) for finding in findings] == [
        ("missing-feature-package", "help"),
        ("undeclared-feature-package", "unbounded"),
    ]
