from __future__ import annotations

from psi_agent._yaml import parse_yaml_header


def test_valid_header_extracts_dict_and_body() -> None:
    content = '---\nname: foo\ncron: "0 0 * * *"\n---\nbody text here'
    header, body = parse_yaml_header(content)
    assert header == {"name": "foo", "cron": "0 0 * * *"}
    assert body == "body text here"


def test_no_header_returns_none_and_original() -> None:
    content = "no front matter\njust body"
    header, body = parse_yaml_header(content)
    assert header is None
    assert body == content


def test_malformed_yaml_returns_none_and_original() -> None:
    content = "---\nfoo: [unclosed\n---\nbody"
    header, body = parse_yaml_header(content)
    assert header is None
    assert body == content


def test_non_dict_header_returns_none_and_original() -> None:
    content = "---\n- a\n- b\n---\nbody"
    header, body = parse_yaml_header(content)
    assert header is None
    assert body == content


def test_header_must_be_at_start_of_content() -> None:
    content = "prefix\n---\nname: foo\n---\nbody"
    header, body = parse_yaml_header(content)
    assert header is None
    assert body == content
