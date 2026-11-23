"""Tests for static checks over added diff lines.

All secrets below are fake values made up for tests; no real keys.
"""

import pytest

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


# --- v0.3.0: Shannon entropy, placeholder filtering, inline ignore ---------


def test_shannon_entropy_known_values():
    from pr_sentinel.static_checks import shannon_entropy

    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaa") == 0.0
    assert shannon_entropy("ab") == pytest.approx(1.0)
    assert shannon_entropy("abcdef") == pytest.approx(2.584962500721156)
    assert shannon_entropy("hunter2") == pytest.approx(2.807354922057604)
    assert shannon_entropy("aabbcc") == pytest.approx(1.584962500721156)


def test_ignore_marker_suppresses_only_its_own_line():
    # The marker lives inside the fixture string itself here, so the
    # first three added lines opt out; the bare eval() on the last line
    # is the only finding. Line numbers: the context line is 1, the
    # added lines are 2-5.
    lines = [
        'api_key = "sk-' + "F" * 32 + '"  # pr-sentinel: ignore',
        "import os  # PR-SENTINEL: IGNORE",
        'x = eval("1")  # pr-sentinel: ignore',
        'y = eval("2")',  # pr-sentinel: ignore
    ]
    findings = _check(lines)
    assert len(findings) == 1
    assert findings[0].rule == "dangerous-eval"
    assert findings[0].line == 5


def test_ignore_marker_works_with_js_and_c_comment_styles():
    lines = [
        'const key = "sk-' + "F" * 32 + '"; // pr-sentinel: ignore',
        'const other = "sk-' + "F" * 32 + '"; /* pr-sentinel: ignore */',
        'const real = "sk-' + "F" * 32 + '";',  # pr-sentinel: ignore
    ]
    findings = _check(lines)
    assert len(findings) == 1
    assert findings[0].rule == "leaked-openai-key"
    assert findings[0].line == 4


def test_ignore_marker_without_comment_does_not_suppress():
    # The marker words inside a string literal (no comment opener on
    # the line) must not suppress a real finding.
    line = (
        'api_key = "sk-' + "F" * 32 + '" + " pr-sentinel: ignore"'
    )  # pr-sentinel: ignore
    findings = _check([line])
    assert len(findings) == 1
    assert findings[0].rule == "leaked-openai-key"


def test_ignore_marker_inside_string_does_not_suppress():
    # The marker inside a quoted string is not a comment: the eval()
    # on the same line is still caught.
    findings = _check(['x = "# pr-sentinel: ignore"; eval(u)'])
    assert len(findings) == 1
    assert findings[0].rule == "dangerous-eval"


def test_ignore_marker_survives_apostrophe_in_comment():
    # An apostrophe in the comment ("don't") must not swallow the
    # marker: the quote is only a string delimiter when it closes.
    findings = _check(['x = eval("1")  # don\'t pr-sentinel: ignore'])
    assert findings == []


def test_ignore_marker_inside_multiline_string_does_not_suppress():
    # A marker inside a triple-quoted string is string content, not a
    # comment: the string closes on the same line before "; eval(u)",
    # and the eval() is still caught. (The markers on the source lines
    # are Python comments outside the fixture strings, so the checked
    # diff text is unchanged; they keep our own review of this PR
    # from flagging the fixtures themselves.)
    lines = [
        's = """',
        '# pr-sentinel: ignore"""; eval(u)',  # pr-sentinel: ignore
    ]
    findings = _check(lines)
    assert len(findings) == 1
    assert findings[0].rule == "dangerous-eval"
    assert findings[0].line == 3


def test_ignore_marker_on_middle_line_of_multiline_string():
    # The marker on a middle line of a triple-quoted string must not
    # suppress anything: the eval() after the string closes fires.
    lines = [
        's = """',
        "# pr-sentinel: ignore",
        '"""; eval(u)',  # pr-sentinel: ignore
    ]
    findings = _check(lines)
    assert len(findings) == 1
    assert findings[0].rule == "dangerous-eval"
    assert findings[0].line == 4


def test_ignore_marker_after_closed_multiline_string_suppresses():
    # Once the triple-quoted string has closed, a real comment marker
    # on the same line still suppresses the finding.
    lines = [
        's = """',
        'doc"""; eval(u)  # pr-sentinel: ignore',
    ]
    findings = _check(lines)
    assert findings == []


def test_ignore_marker_inside_single_quote_multiline_string():
    # The same multiline behaviour with ''' delimiters.
    lines = [
        "s = '''",
        "# pr-sentinel: ignore'''; eval(u)",  # pr-sentinel: ignore
    ]
    findings = _check(lines)
    assert len(findings) == 1
    assert findings[0].rule == "dangerous-eval"


def test_real_words_ending_in_placeholder_are_not_suppressed():
    # "contest" / "protest" / "latest" end in "test" but are real words,
    # not fixtures — they must still fire. (The markers on the source
    # lines are Python comments outside the fixture strings, so the
    # checked diff text is unchanged; they keep our own review of this
    # PR from flagging the fixtures themselves.)
    lines = [
        'password = "contest"',  # pr-sentinel: ignore
        'password = "protest"',  # pr-sentinel: ignore
        'password = "latest"',  # pr-sentinel: ignore
        'token = "snowflake"',  # pr-sentinel: ignore
    ]
    findings = _check(lines)
    assert [f.rule for f in findings] == ["hardcoded-credential"] * 4


def test_placeholder_word_with_digits_is_suppressed():
    # "test123" is the canonical fixture shape: a placeholder word
    # followed by digits. (It fired before this fix; the docstring
    # claimed otherwise.)
    findings = _check(['password = "test123"'])  # pr-sentinel: ignore
    assert findings == []


def test_placeholder_values_are_suppressed():
    lines = [
        'token = "ghp_test"',
        'key = "AKIA' + "IOSFODNN7EXAMPLE" + '"',
        'password = "changeme"',
        'api_key = "your_api_key_here"',
        'token = "xxxx"',
        'token = "aabbcc"',
        'key = "sk-proj-testtesttesttesttest"',
    ]
    assert _check(lines) == []


def test_realistic_secrets_still_fire():
    # Regression tests: these look like real leaked secrets and must
    # never be suppressed, even though they share prefixes with the
    # placeholder examples above. (The marker on each source line is
    # a Python comment outside the fixture string, so the checked
    # diff text is unchanged; it keeps our own review of this PR
    # from flagging the fixtures themselves.)
    lines = [
        'api_key = "sk-'
        + "K7mQ2xR9vT4wY8pL3nB6cD1fG5hJ0kM"
        + '"',  # pr-sentinel: ignore
        'token = "ghp_'
        + "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY3zA5bC"
        + '"',  # pr-sentinel: ignore
        'key = "AKIA' + "K7M2Q9X4V8B3N6P1R" + '"',  # pr-sentinel: ignore
        "xoxb-" + "483920175638-1a2B3c4D5e6F7g8H9i0J",  # pr-sentinel: ignore
    ]
    findings = _check(lines)
    assert [f.rule for f in findings] == [
        "leaked-openai-key",
        "leaked-github-token",
        "leaked-aws-key",
        "leaked-slack-token",
    ]


def test_fake_substring_key_still_fires():
    # "sk-fake..." contains "fake" only as a substring of a longer
    # component, not as a whole component or a suffix — it is a
    # realistic key and must still fire.
    findings = _check(
        ['api_key = "sk-' + "fake1234567890abcdefghij" + '"]']
    )  # pr-sentinel: ignore
    assert len(findings) == 1
    assert findings[0].rule == "leaked-openai-key"


def test_private_key_header_is_never_a_placeholder():
    # The dashes in a private key header would trip the repeated-char
    # check, so the rule is exempt from placeholder filtering outright.
    findings = _check(["-----BEGIN PRIVATE KEY-----"])
    assert len(findings) == 1
    assert findings[0].rule == "leaked-private-key"
