from app.domain.graph import Edge, Graph, Node, default_graph, holds, render, validate


def g(nodes, edges):
    return Graph([Node(*n) if isinstance(n, tuple) else n for n in nodes], [Edge(*e) for e in edges])


def test_the_default_graph_is_valid_and_reads_straight_through():
    d = default_graph()
    assert validate(d) == [] and [n.type for n in d.nodes] == ["trigger", "scope", "prompt", "agent", "output"]
    assert Graph.from_json(d.to_json()).to_json() == d.to_json()


def test_a_branch_needs_an_edge_for_each_case_and_an_else():
    nodes = [("t", "trigger"), ("b", "branch", "Is it about money?", {"cases": [{"var": "input", "op": "contains", "value": "money", "label": "money"}]}), ("a", "agent"), ("a2", "agent"), ("o", "output")]
    ok = g(nodes, [("t", "b"), ("b", "a", "money"), ("b", "a2", "else"), ("a", "o"), ("a2", "o")])
    assert validate(ok) == []
    missing = g(nodes, [("t", "b"), ("b", "a", "money"), ("a", "o"), ("a2", "o")])
    problems = validate(missing)
    assert any("one labelled else" in p for p in problems) and any("can't be reached" in p for p in problems)


def test_a_cycle_is_allowed_only_through_a_loop_and_a_loop_needs_again_and_done():
    nodes = [("t", "trigger"), ("a", "agent"), ("l", "loop", "Refine", {"max": 3}), ("o", "output")]
    assert validate(g(nodes, [("t", "a"), ("a", "l"), ("l", "a", "again"), ("l", "o", "done")])) == []
    circle = g([("t", "trigger"), ("a", "agent"), ("s", "set", "x", {"name": "x"}), ("o", "output")], [("t", "a"), ("a", "s"), ("s", "a"), ("a", "o", "else")])
    assert any("circle" in p for p in validate(circle))
    assert any("again" in p for p in validate(g(nodes, [("t", "a"), ("a", "l"), ("l", "o", "done")])))


def test_what_makes_a_graph_unrunnable_is_said_in_words():
    assert "A workflow needs exactly one trigger." in validate(g([("a", "agent"), ("o", "output")], [("a", "o")]))
    assert any("agent node" in p for p in validate(g([("t", "trigger"), ("o", "output")], [("t", "o")])))
    assert any("leads nowhere" in p for p in validate(g([("t", "trigger"), ("a", "agent"), ("o", "output")], [("t", "a")])))
    assert any("ends the run" in p for p in validate(g([("t", "trigger"), ("a", "agent"), ("o", "output")], [("t", "a"), ("a", "o"), ("o", "a")])))
    assert any("fixed scope" in p.lower() for p in validate(g([("t", "trigger"), ("s", "scope", "", {"mode": "fixed", "workspace": "nowhere"}), ("a", "agent"), ("o", "output")], [("t", "s"), ("s", "a"), ("a", "o")])))


def test_conditions_and_templates_read_the_run_so_far_and_never_raise():
    s = {"input": "Pay the Money", "answer": "", "found": 2, "workspace": "sb", "vars": {"tone": "formal"}, "nodes": {"agent": "done"}}
    assert holds({"var": "input", "op": "contains", "value": "money"}, s) and holds({"var": "answer", "op": "empty"}, s) and holds({"var": "found", "op": "gt", "value": 1}, s)
    assert holds({"var": "vars.tone", "op": "equals", "value": "FORMAL"}, s) and holds({"var": "input", "op": "matches", "value": "^pay"}, s)
    assert not holds({"var": "input", "op": "matches", "value": "(["}, s) and not holds({"var": "input", "op": "nope"}, s) and not holds({"var": "x", "op": "gt", "value": "a"}, s)
    assert render("{{input}} / {{workspace}} / {{vars.tone}} / {{nodes.agent}} / {{missing}}!", s) == "Pay the Money / sb / formal / done / !"
