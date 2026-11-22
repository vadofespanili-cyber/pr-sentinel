"""Tests for static checks over added diff lines.

All secrets below are fake values made up for tests; no real keys.
"""

from pr_sentinel.diff import parse_diff
from pr_sentinel.static_checks import run_static_checks


def _diff_with_added(lines: list[str]) -> str:
    body = "".join(f"+{line}\n" for line in lines)
    return (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        f"@@ -1,1 +1,{len(lines)} @@\n"
        " context\n" + body
    )


def _check(lines: list[str], **kwargs):
    return run_static_checks(parse_diff(_diff_with_added(lines)), **kwargs)


def _rules(findings):
    return [f.rule for f in findings]


def test_openai_key_fires():
    findings = _check(['api_key = "sk-' + "F" * 32 + '"'])
    assert "leaked-openai-key" in _rules(findings)
    assert findings[0].severity == "critical"
    assert findings[0].category == "security"
    assert findings[0].file == "app.py"
    assert findings[0].line == 2
    # The secret value itself must not be echoed in the message.
    assert "F" * 32 not in findings[0].message


def test_openai_project_key_fires():
    findings = _check(['api_key = "sk-proj-' + "F" * 32 + '"'])
    assert "leaked-openai-key" in _rules(findings)
    assert findings[0].severity == "critical"
    assert findings[0].category == "security"
    assert findings[0].file == "app.py"
    # The secret value itself must not be echoed in the message.
    assert "F" * 32 not in findings[0].message
    # Must not double-report via the generic credential rule.
    assert "hardcoded-credential" not in _rules(findings)


def test_github_token_fires():
    for token in [
        "ghp_" + "F" * 36,
        "gho_" + "F" * 36,
        "github_pat_" + "F" * 22 + "_" + "F" * 59,
    ]:
        assert "leaked-github-token" in _rules(_check([f'token = "{token}"']))


def test_aws_key_fires():
    findings = _check(['key = "AKIA' + "F" * 16 + '"'])
    assert "leaked-aws-key" in _rules(findings)
    assert findings[0].severity == "critical"


def test_slack_token_fires():
    findings = _check(['token = "xoxb-' + "F" * 12 + '"'])
    assert "leaked-slack-token" in _rules(findings)


def test_private_key_fires():
    for header in [
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
    ]:
        assert "leaked-private-key" in _rules(_check([header]))


def test_generic_hardcoded_credential_fires():
    for line in [
        'password = "hunter2"',
        "passwd = 'hunter2'",
        'SECRET = "abc123"',
        'api_key = "abcdef"',
        'api-key = "abcdef"',
        'token = "abcdef"',
    ]:
        assert "hardcoded-credential" in _rules(_check([line])), line
    # Too short a value does not fire.
    assert "hardcoded-credential" not in _rules(_check(['token = "abc"']))


def test_specific_secret_suppresses_generic_credential():
    # A line with a specific secret (OpenAI key) must not also report the
    # generic hardcoded-credential rule for the same line.
    line = 'api_key = "sk-fake1234567890abcdefghij"'
    rules = _rules(_check([line]))
    assert "leaked-openai-key" in rules
    assert "hardcoded-credential" not in rules


def test_eval_exec_fire():
    assert "dangerous-eval" in _rules(_check(["eval(user_input)"]))
    assert "dangerous-exec" in _rules(_check(["exec(code)"]))
    findings = _check(["eval(user_input)"])
    assert findings[0].severity == "high"
    assert findings[0].category == "security"


def test_os_system_and_shell_true_fire():
    assert "dangerous-os-system" in _rules(_check(['os.system("ls")']))
    assert "dangerous-shell-true" in _rules(_check(["subprocess.run(cmd, shell=True)"]))
    assert "dangerous-shell-true" in _rules(
        _check(["subprocess.Popen(cmd, shell = True)"])
    )
    # shell=False is fine.
    assert _check(['subprocess.run(["ls"], shell=False)']) == []


def test_pickle_fires():
    assert "dangerous-pickle-load" in _rules(_check(["pickle.loads(data)"]))
    assert "dangerous-pickle-load" in _rules(_check(["pickle.load(f)"]))


def test_yaml_load_without_loader_fires():
    findings = _check(["yaml.load(stream)"])
    assert "dangerous-yaml-load" in _rules(findings)
    assert findings[0].severity == "medium"
    assert findings[0].category == "security"
    # With an explicit Loader it is fine.
    assert _check(["yaml.load(stream, Loader=yaml.SafeLoader)"]) == []
    assert _check(["yaml.safe_load(stream)"]) == []


def test_clean_diff_gives_no_findings():
    findings = _check(
        [
            "import os",
            "def greet(name):",
            '    return f"hello {name}"',
            "x = 1 + 2",
        ]
    )
    assert findings == []


def test_large_file_fires():
    lines = [f"x{i} = {i}" for i in range(5)]
    findings = _check(lines, max_added_lines=3)
    large = [f for f in findings if f.rule == "large-file"]
    assert len(large) == 1
    assert large[0].severity == "medium"
    assert large[0].category == "maintainability"
    assert "5" in large[0].message
    assert large[0].line == 2  # first added line


def test_large_file_does_not_fire_at_limit():
    lines = [f"x{i} = {i}" for i in range(3)]
    assert _check(lines, max_added_lines=3) == []


def test_deleted_and_context_lines_do_not_fire():
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,3 +1,2 @@\n"
        '-api_key = "sk-' + "F" * 32 + '"\n'
        "-eval(user_input)\n"
        " context with sk-" + "F" * 32 + " in it\n"
        "+safe = True\n"
    )
    findings = run_static_checks(parse_diff(diff))
    assert findings == []


def test_finding_line_numbers_follow_new_file():
    findings = _check(["ok = 1", "eval(x)", "ok = 2"])
    assert [f.line for f in findings if f.rule == "dangerous-eval"] == [3]
