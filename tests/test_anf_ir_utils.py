from myia.anf_ir import Constant, Apply, Graph
from myia.graph_utils import dfs as _dfs
from myia.anf_ir_utils import \
    dfs, toposort, accessible_graphs, destroy_disconnected_nodes, \
    is_constant_graph, \
    succ_incoming, succ_deep, succ_bidirectional

from myia.api import ENV
from myia.parser import Parser

from .test_graph_utils import _check_toposort


def test_dfs():
    in0 = Constant(0)
    in1 = Constant(1)
    value = Apply([in0, in1], Graph())
    assert next(dfs(value)) == value
    assert set(dfs(value)) == {value, in0, in1}


def test_dfs_graphs():
    g0 = Graph()
    in0 = Constant(g0)
    in1 = Constant(1)
    g0.return_ = in1
    value = Apply([in0], Graph())
    assert set(dfs(value)) == {value, in0}
    assert set(dfs(value, follow_graph=True)) == {value, in0, in1}


def test_toposort():
    g0 = Graph()
    g0.output = Constant(1)
    g1 = Graph()
    in0 = Constant(g0)
    value = Apply([in0], g1)
    g1.output = value

    order = list(toposort(g1.return_))
    _check_toposort(order, g1.return_, succ_incoming)


def test_toposort2():
    g0 = Graph()
    g0.output = Constant(33)
    g1 = Graph()
    in0 = Constant(g0)
    in1 = Constant(1)
    v1 = Apply([in0, in1], g1)
    v2 = Apply([in0, v1, in1], g1)
    g1.output = v2

    order = list(toposort(g1.return_))
    _check_toposort(order, g1.return_, succ_incoming)


def _name_nodes(nodes):
    def name(node):
        if is_constant_graph(node):
            return node.value.debug.name or '.'
        elif isinstance(node, Constant):
            return str(node.value)
        else:
            return node.debug.name or '.'

    return set(map(name, nodes))


def test_disconnect():
    def f(x):
        a = x * x
        _b = a + x  # Not connected to any output # noqa
        c = a * a
        d = c * c   # Connected to g's output

        def g(y):
            return d * y
        return g(c)

    g = Parser(ENV, f).parse(False)

    # Include {None} to get Constants (makes it easier to compare to live)
    cov = {None} | accessible_graphs(g)

    live = _name_nodes(_dfs(g.return_, succ_deep))
    assert live == set('x a mul c return . g d y'.split())

    total = _name_nodes(_dfs(g.return_, succ_bidirectional(cov)))
    assert total == set('x a mul c return . g d y _b add'.split())

    destroy_disconnected_nodes(g)

    total2 = _name_nodes(_dfs(g.return_, succ_bidirectional(cov)))
    assert total2 == live
