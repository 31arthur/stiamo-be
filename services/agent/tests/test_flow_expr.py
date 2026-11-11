import pytest
from app.domain.flow_expr import ExpressionError, evaluate, resolve

S = {
    "$json": {"subject": "Pay the invoice", "sender": {"name": "Hemla", "email": "h@x.org"}, "tags": ["money", "urgent"], "n": 3},
    "$input": [{"subject": "a"}, {"subject": "b"}],
    "$node": {"Mail": {"items": [{"subject": "one"}, {"subject": "two"}], "first": {"subject": "one"}, "count": 2}},
    "$workspace": "sb",
    "$vars": {"tone": "formal"},
}


def test_paths_reach_into_the_item_the_inputs_and_earlier_nodes():
    assert evaluate("$json.sender.name", S) == "Hemla" and evaluate("$json.tags[1]", S) == "urgent" and evaluate("$json.tags.length", S) == 2
    assert evaluate("$input.length", S) == 2 and evaluate("$input[0].subject", S) == "a" and evaluate("$input.last.subject", S) == "b"
    assert evaluate('$node["Mail"].first.subject', S) == "one" and evaluate('$node["Mail"].count', S) == 2 and evaluate("$vars.tone", S) == "formal"
    assert evaluate("$json.missing.deeper", S) is None and evaluate("$workspace", S) == "sb"  # a field that isn't there is empty, not an error


def test_comparison_logic_and_functions_read_like_the_n8n_ones():
    assert evaluate('contains($json.subject, "invoice") && $json.n > 2', S) is True
    assert evaluate('$json.n >= 4 || startsWith($json.subject, "pay")', S) is True and evaluate("!($json.n == 3)", S) is False
    assert evaluate('$json.sender.name == "hemla"', S) is True  # words compare without caring about case
    assert evaluate('join($json.tags, " / ")', S) == "money / urgent" and evaluate("len($json.tags) + 1", S) == 3
    assert evaluate('default($json.nothing, "none")', S) == "none" and evaluate("isEmpty($json.nothing)", S) is True
    assert evaluate('matches($json.subject, "^pay .* invoice$")', S) is True and evaluate('upper("a") + lower("B")', S) == "Ab"


def test_a_whole_expression_keeps_its_type_and_text_around_it_is_written_in():
    assert resolve("{{ $json.tags }}", S) == ["money", "urgent"] and resolve("{{ $json.n }}", S) == 3
    assert resolve("Hi {{ $json.sender.name }}, {{ $input.length }} new", S) == "Hi Hemla, 2 new"
    assert resolve({"a": ["{{ $json.n + 1 }}", "x"], "b": {"c": "{{$workspace}}"}}, S) == {"a": [4, "x"], "b": {"c": "sb"}}
    assert resolve("plain", S) == "plain" and resolve(7, S) == 7


def test_what_cannot_be_read_is_said_in_words_and_nothing_is_run_as_code():
    for bad in ("$json.", "contains($json.subject", "nope(1)", "1 +", "__import__('os')", "$json.n ** 2"):
        with pytest.raises(ExpressionError):
            evaluate(bad, S)
    with pytest.raises(ExpressionError, match="isn't a number"):
        evaluate('"abc" - 1', S)
    with pytest.raises(ExpressionError, match="too long"):
        evaluate("1+" * 300 + "1", S)
