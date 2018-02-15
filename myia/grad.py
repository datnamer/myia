
from typing import Set
from collections import defaultdict
from functools import reduce
from myia.anf_ir import Apply, Constant, Parameter, Graph, ANFNode
from myia.info import About
from myia import primops
from myia.anf_ir_utils import \
    dfs, is_apply, is_constant, is_constant_graph
from myia.cconv import NestingAnalyzer


add = Constant(primops.add)
J = Constant(primops.J)
index = Constant(primops.getitem)
cons = Constant(primops.cons_tuple)


class Grad:
    """Gradient transform on Graphs.

    When given a root graph, this will transform it and every graph nested in
    it. For each graph g we make the graphs ↑g and ♢g (forward graph and
    backpropagator). All forward graphs are made before all backpropagator
    graphs.
    """

    def __init__(self) -> None:
        """Initialize Grad."""

        # Accumulate graphs to process forward or backward in these sets
        self.todo_fw: Set[Graph] = set()
        self.todo_bw: Set[Graph] = set()
        # Graphs that we are done with for the forward pass
        self.done_fw: Set[Graph] = set()

        # Each graph is mapped to an *ordered* list of free variables
        self.fv_order: Dict[Graph, List[ANFNode]] = {}

        # g -> ↑g
        self.tagged_graphs: Dict[Graph, Graph] = {}
        # g -> ♢g
        self.backpropagator_graphs: Dict[Graph, Graph] = {}

        # x -> ↑x
        self.tagged_nodes: Dict[ANFNode, ANFNode] = {}
        # x -> ♢x
        self.backpropagator_nodes: Dict[ANFNode, ANFNode] = {}
        # (x, g) -> ∇x (in the context of that graph)
        self.sensitivity_nodes: Dict[Tuple[ANFNode, Graph], ANFNode] = {}
        # x -> ♢x(∇x) (for x an Apply node)
        self.step_nodes: Dict[Apply, Apply] = {}

        # To get the uses of a graph, we need to know which Constant(s)
        # refer to that graph, so we keep that in this map.
        self.graph_to_ct: Dict[Graph, Set[Constant]] = defaultdict(set)

    def scaffold_graph(self, graph):
        """Prepare the forward and backpropagator graphs for this graph."""

        # Get info about free variables and order them

        # self.nest.run(graph)
        self.fv_order[graph] = list(self.nest.free_variables_total()[graph])

        # Forward graph
        with About(graph, 'grad_fw'):
            tgraph = Graph()
        graph.grad = tgraph
        tgraph.primal = graph
        self.tagged_graphs[graph] = tgraph
        # Same parameters as the original, but tagged
        for p in graph.parameters:
            with About(p, 'grad_fw'):
                tp = Parameter(tgraph)
            tgraph.parameters.append(tp)
            self.tagged_nodes[p] = tp

        # Backpropagator graph
        with About(graph, 'grad_bprop'):
            bgraph = Graph()
        self.backpropagator_graphs[graph] = bgraph
        # Takes output sensitivity as sole parameter
        with About(graph, 'grad_bw'):
            bparam = Parameter(bgraph)
        bgraph.parameters.append(bparam)
        self.sensitivity_nodes[(graph.output, graph)] = bparam

        self.todo_fw.add(graph)
        self.todo_bw.add(graph)

        return Constant(tgraph), Constant(bgraph)

    def process_graph(self, graph):
        """Process this graph and return the forward graph.
        
        This may not work if called more than once with different
        graphs. Create a new Grad instance instead.
        """
        if hasattr(graph, 'grad'):
            return graph.grad

        # We use this to get the list of free variables for each graph
        self.nest = NestingAnalyzer(graph)

        self.process_all_graphs_forward(graph)
        self.process_all_graphs_backward()
        return self.tagged_graphs[graph]

    def make_cons(self, graph, *elems):
        if len(elems) == 0:
            return Constant(())
        else:
            x, *rest = elems
            return self.apply(graph,
                              primops.cons_tuple,
                              x,
                              self.make_cons(graph, *rest))

    def apply(self, graph, *inputs):
        inputs = [i if isinstance(i, ANFNode) else Constant(i)
                  for i in inputs]
        return Apply(inputs, graph)

    def process_graph_forward(self, graph):
        """Create the forward graph."""
        if graph in self.done_fw:
            return

        if graph not in self.tagged_graphs:
            self.scaffold_graph(graph)

        tgraph = self.tagged_graphs[graph]
        bgraph = self.backpropagator_graphs[graph]

        # Return (↑graph.output, ♢graph). The first element is given
        # by the `phi` method.

        tgraph.output = self.make_cons(
            tgraph,
            self.phi(graph.output),
            Constant(bgraph)
        )

        self.done_fw.add(graph)

    def process_all_graphs_forward(self, root):
        """Create the forward graph for all graphs starting from root."""
        self.todo_fw.add(root)
        while self.todo_fw:
            g = self.todo_fw.pop()
            self.process_graph_forward(g)

    def process_graph_backward(self, graph):
        """Create the backward graph."""
        bgraph = self.backpropagator_graphs[graph]

        # Return ((∇fv1, ∇fv2, ...), ∇arg1, ∇arg2, ...)
        # Where ∇x is given by `rho(x, graph)`

        bgraph.output = self.make_cons(
            bgraph,
            self.make_cons(bgraph,
                           *[self.rho(p, graph) for p in self.fv_order[graph]]),
            *[self.rho(p, graph) for p in graph.parameters]
        )

    def process_all_graphs_backward(self):
        """Create the backward graph for all graphs.

        This method has no argument: it simply processes all graphs for which we
        created a forward graph.
        """
        for g in self.todo_bw:
            self.process_graph_backward(g)

    def phi(self, node):
        """Compute equivalent node in forward graph."""
        if node in self.tagged_nodes:
            return self.tagged_nodes[node]

        tg = node.graph and self.tagged_graphs[node.graph]

        if is_constant_graph(node):
            # We will have to process this graph too.
            tagged, bprop = self.scaffold_graph(node.value)
            self.graph_to_ct[node.value].add(node)
        elif is_constant(node):
            # Note that this application will have its graph set to None, which
            # makes sense since it's basically a constant expression.
            tagged, bprop = self.apply(tg, J, node), None
        elif is_apply(node):
            # a = f(x, y) -> ↑a, ♢a = ↑f(↑x, ↑y)
            tagged_args = [self.phi(n) for n in node.inputs]
            app = self.apply(tg, *tagged_args)
            # ↑a (the first element)
            tagged = self.apply(tg, index, app, 0)
            # ♢a (the second element)
            # Note that ♢a is not part of the forward graph, however,
            # it will be a free variable of the backpropagator graph.
            bprop = self.apply(tg, index, app, 1)
        else:
            # Note: Parameters were all added to tagged_nodes in
            # scaffold_graph, so they won't trigger this branch.
            raise Exception('This should be unreachable.')

        self.tagged_nodes[node] = tagged
        self.backpropagator_nodes[node] = bprop
        tagged.debug.about = About(node, 'grad_fw')
        if bprop:
            bprop.debug.about = About(node, 'grad_bprop')
        return tagged

    def bprop_step(self, node):
        """Compute backpropagator expression for this node.

        If node is a = f(x, y), this returns ♢a(∇a). That expression returns
        gradient contributions to ∇f, ∇x and ∇y.
        """
        if node in self.step_nodes:
            return self.step_nodes[node]

        bg = node.graph and self.backpropagator_graphs[node.graph]
        bprop = self.backpropagator_nodes[node]
        if bprop:
            rval = self.apply(bg, bprop, self.rho(node, node.graph))
            self.step_nodes[node] = rval
            rval.debug.about = About(node, 'grad_bprop_step')
            return rval
        else:
            return None

    def rho(self, node, graph):
        """Compute expression for gradient wrt node and graph."""

        # We index with node and graph because the same node may have multiple
        # sensitivity variables, one for each graph that refers to the original.
        key = (node, graph)
        if key in self.sensitivity_nodes:
            return self.sensitivity_nodes[key]

        bg = self.backpropagator_graphs[graph]

        # We will accumulate all gradient contributions here.
        contribs = []

        for user, idx in node.uses:
            # Each use of a variable contributes to its gradient.
            if user.graph is graph:
                # A use in the same graph: we get the backpropagator expression
                # and we use the argument index to extract the right
                # contribution.
                with About(node.debug, 'grad_bw'):
                    contrib = self.apply(bg, index, self.bprop_step(user), idx)
                contribs.append(contrib)
            else:
                # Uses in different graphs are ignored here. We take them
                # into account below.
                pass

        # We list all graphs that are immediately nested in this one and have
        # this node as a free variable. These graphs may not technically contain
        # a use of node, but they contain graphs that do, so there is a gradient
        # of the node with respect to them.
        children = {g for g in self.nest.coverage()
                    if self.nest.parents()[g] is graph
                    and node in self.nest.free_variables_total()[g]}

        for child in children:
            # This is the index of this node in the graph's free
            # variables.
            idx = self.fv_order[child].index(node)
            for graph_ct in self.graph_to_ct[child]:
                # We get the sensitivity wrt the closure using `rho` on the
                # constant and this graph. Concretely this means we will
                # look at the uses of the closure in this graph. Or if the
                # closure is returned, this will be the sensitivity wrt the
                # output. We index this sensitivity with idx to get the
                # contribution we seek.
                contrib = self.apply(bg,
                                     index,
                                     self.rho(graph_ct, graph),
                                     idx)
                contribs.append(contrib)

        # NOTE: The order of nodes in contribs is not deterministic, because
        # the order of users isn't. In theory that doesn't matter, because we
        # add them all, but in practice there could be slight numerical
        # differences.

        if len(contribs) == 0:
            # No contributions means a gradient of zero, naturally.
            sens = self.apply(bg, primops.zeros_like, self.tagged_nodes[node])
        else:
            # Contributions must be added together.
            def mkadd(x, y):
                return self.apply(bg, add, x, y)
            sens = reduce(mkadd, contribs)

        sens.debug.about = About(node.debug, 'grad_bw')
        self.sensitivity_nodes[node] = sens
        return sens
