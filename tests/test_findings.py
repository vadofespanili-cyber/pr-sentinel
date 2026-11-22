"""Tests for the Finding model, sorting and filtering."""

import pytest

from pr_sentinel.findings import Finding, filter_findings, sort_findings


def _finding(severity="medium", category="bug", file="a.py", line=1):
    return Finding(
        severity=severity,
        category=category,
        file=file,
        line=line,
        message=f"{severity} in {file}:{line}",
    )


def test_finding_validation_errors():
    with pytest.raises(ValueError):
        Finding("fatal", "bug", "a.py", 1, "bad severity")
    with pytest.raises(ValueError):
        Finding("high", "typo", "a.py", 1, "bad category")
    with pytest.raises(ValueError):
        Finding("high", "bug", "a.py", 0, "line zero")
    with pytest.raises(ValueError):
        Finding("high", "bug", "a.py", -3, "negative line")


def test_finding_allows_null_line_and_defaults():
    finding = Finding("low", "style", "a.py", None, "file-level note")
    assert finding.line is None
    assert finding.suggestion == ""
    assert finding.rule == ""
    assert finding.to_dict()["severity"] == "low"


def test_filter_threshold_is_inclusive():
    findings = [
        _finding("critical"),
        _finding("high"),
        _finding("medium"),
        _finding("low"),
    ]
    assert [f.severity for f in filter_findings(findings, "medium")] == [
        "critical",
        "high",
        "medium",
    ]
    assert [f.severity for f in filter_findings(findings, "critical")] == ["critical"]
    assert len(filter_findings(findings, "low")) == 4
    assert filter_findings(findings, "high") == findings[:2]


def test_filter_unknown_threshold_raises():
    with pytest.raises(ValueError):
        filter_findings([_finding()], "fatal")


def test_filter_ignore_path_globs():
    findings = [
        _finding(file="src/main.py"),
        _finding(file="vendor/lib.js"),
        _finding(file="dist/app.min.js"),
        _finding(file="src/util.min.js"),
    ]
    kept = filter_findings(findings, "low", ignore_paths=["vendor/**", "*.min.js"])
    assert [f.file for f in kept] == ["src/main.py"]


def test_filter_combines_threshold_and_ignore():
    findings = [
        _finding("low", file="src/main.py"),
        _finding("high", file="vendor/lib.js"),
    ]
    kept = filter_findings(findings, "medium", ignore_paths=["vendor/**"])
    assert kept == []


def test_sort_findings_order():
    findings = [
        _finding("low", file="b.py", line=5),
        _finding("medium", file="b.py", line=2),
        _finding("critical", file="a.py", line=9),
        _finding("high", file="a.py", line=1),
        _finding("high", file="a.py", line=None),
        _finding("medium", file="a.py", line=3),
    ]
    ordered = sort_findings(findings)
    assert [(f.severity, f.file, f.line) for f in ordered] == [
        ("critical", "a.py", 9),
        ("high", "a.py", None),
        ("high", "a.py", 1),
        ("medium", "a.py", 3),
        ("medium", "b.py", 2),
        ("low", "b.py", 5),
    ]
