"""Implementations for the debug VM."""


from typing import Callable
from copy import copy
from myia import primops
from myia.anf_ir import Graph
from myia.utils import Registry, smap
from myia.vm import VMFrame


implementations: Registry[primops.Primitive, Callable] = Registry()
register = implementations.register


@register(primops.add)
def add(x, y):
    """Implement `add`."""
    return x + y


@register(primops.sub)
def sub(x, y):
    """Implement `sub`."""
    return x - y


@register(primops.mul)
def mul(x, y):
    """Implement `mul`."""
    return x * y


@register(primops.div)
def div(x, y):
    """Implement `div`."""
    return x / y


@register(primops.mod)
def mod(x, y):
    """Implement `mod`."""
    return x % y


@register(primops.pow)
def pow(x, y):
    """Implement `pow`."""
    return x ** y


@register(primops.uadd)
def uadd(x):
    """Implement `iadd`."""
    return x


@register(primops.usub)
def usub(x):
    """Implement `isub`."""
    return -x


@register(primops.eq)
def eq(x, y):
    """Implement `eq`."""
    return x == y


@register(primops.lt)
def lt(x, y):
    """Implement `lt`."""
    return x < y


@register(primops.gt)
def gt(x, y):
    """Implement `gt`."""
    return x > y


@register(primops.ne)
def ne(x, y):
    """Implement `ne`."""
    return x != y


@register(primops.le)
def le(x, y):
    """Implement `le`."""
    return x <= y


@register(primops.ge)
def ge(x, y):
    """Implement `ge`."""
    return x >= y


@register(primops.not_)
def not_(x):
    """Implement `not_`."""
    return not x


@register(primops.make_tuple)
def make_tuple(*elems):
    """Implement `make_tuple`."""
    return elems


@register(primops.getitem)
def getitem(data, item):
    """Implement `getitem`."""
    return data[item]


@register(primops.setitem)
def setitem(data, item, value):
    """Implement `setitem`."""
    data2 = copy(data)
    data2[item] = value
    return data2


py_getattr = getattr
py_setattr = setattr


@register(primops.getattr)
def getattr(data, attr):
    """Implement `getattr`."""
    return py_getattr(data, attr)


@register(primops.setattr)
def setattr(data, attr, value):
    """Implement `setattr`."""
    data2 = copy(data)
    py_setattr(data2, attr, value)
    return data2


@register(primops.return_)
def return_(x):
    """Implement `return_`."""
    return x


@register(primops.J)
def J(x):
    from myia.grad_implementations import implementations
    from myia.anf_ir import Graph
    from myia.grad import Grad

    if isinstance(x, primops.Primitive):
        return implementations[x]
    elif isinstance(x, Graph):
        gr = Grad()
        return gr.process_graph(x)
    elif isinstance(x, (int, float)):
        return x
    else:
        raise TypeError(f'J is not defined on {type(x)}')


@register(primops.Jinv)
def Jinv(x):
    if isinstance(x, (int, float)):
        return x
    else:
        raise TypeError(f'Jinv is not defined on {type(x)}')


class Zero:
    """Null object for addition.

    * ZERO + x is x
    * x + ZERO is x
    * ZERO[i] is ZERO
    """

    def __add__(self, z):
        return z

    def __radd__(self, z):
        return z

    def __getitem__(self, item):
        return self


ZERO = Zero()


@register(primops.zeros_like)
def zeros_like(x):
    def zero(x):
        if isinstance(x, VMFrame.Closure):
            return ZERO
        elif isinstance(x, (Graph, primops.Primitive)):
            return ()
        else:
            return type(x)(0)

    return smap(zero, x)
