# Copyright 2018-2021 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Contains utility functions and decorators for constructing valid transforms."""
# pylint: disable=too-few-public-methods
import functools
import inspect
from typing import Tuple, List, Callable

import pennylane as qml


AUTO_EXECUTE_NO_PROCESSING = (
    qml.tape.QuantumTape,
    List[qml.tape.QuantumTape],
    Tuple[qml.tape.QuantumTape, None],
    Tuple[List[qml.tape.QuantumTape], None]
)
"""tuple[type]: QNode transform function return types
for which the decorator will qutomatically execute returned
tapes. The resulting QNode transform will return a list of floating
point results per tape to be executed.
"""


AUTO_EXECUTE_PROCESSING = (
    inspect._empty,
    Tuple[List[qml.tape.QuantumTape], Callable]
)
"""tuple[type]: QNode transform function return types
for which the decorator will:

- Automatically executes returned tapes
- Applies post-processing functions
"""


AUTO_EXECUTE = AUTO_EXECUTE_PROCESSING + AUTO_EXECUTE_NO_PROCESSING


def make_tape(fn):
    """Returns a function that generates the tape from a quantum function without any
    operation queuing taking place.

    This is useful when you would like to manipulate or transform
    the tape created by a quantum function without evaluating it.

    Args:
        fn (function): the quantum function to generate the tape from

    Returns:
        function: The returned function takes the same arguments as the quantum
        function. When called, it returns the generated quantum tape
        without any queueing occuring.

    **Example**

    Consider the following quantum function:

    .. code-block:: python

        def qfunc(x):
            qml.Hadamard(wires=0)
            qml.CNOT(wires=[0, 1])
            qml.RX(x, wires=0)

    We can use ``make_tape`` to extract the tape generated by this
    quantum function, without any of the operations being queued by
    any existing queuing contexts:

    >>> with qml.tape.QuantumTape() as active_tape:
    ...     qml.RY(1.0, wires=0)
    ...     tape = make_tape(qfunc)(0.5)
    >>> tape.operations
    [Hadamard(wires=[0]), CNOT(wires=[0, 1]), RX(0.5, wires=[0])]

    Note that the currently recording tape did not queue any of these quantum operations:

    >>> active_tape.operations
    [RY(1.0, wires=[0])]
    """

    def wrapper(*args, **kwargs):
        active_tape = qml.tape.get_active_tape()

        if active_tape is not None:
            with active_tape.stop_recording(), active_tape.__class__() as tape:
                fn(*args, **kwargs)
        else:
            with qml.tape.QuantumTape() as tape:
                fn(*args, **kwargs)
        return tape

    return wrapper


class NonQueuingTape(qml.queuing.AnnotatedQueue):
    """Mixin class that creates a tape that does not queue
    itself to the current queuing context."""

    def _process_queue(self):
        super()._process_queue()

        for obj, info in self._queue.items():
            qml.queuing.QueuingContext.append(obj, **info)

        qml.queuing.QueuingContext.remove(self)


class single_tape_transform:
    """For registering a tape transform that takes a tape and outputs a single new tape.

    Examples of such transforms include circuit compilation.

    Args:
        transform_fn (function): The function to register as the single tape transform.
            It can have an arbitrary number of arguments, but the first argument
            **must** be the input tape.

    **Example**

    A valid single tape transform is a quantum function that satisfies the following:

    - The first argument must be an input tape

    - Depending on the structure of this input tape, various quantum operations, functions,
      and templates may be called.

    - Any internal classical processing should use the ``qml.math`` module to ensure
      the transform is differentiable.

    - There is no return statement.

    For example:

    .. code-block:: python

        @qml.single_tape_transform
        def my_transform(tape, x, y):
            # loop through all operations on the input tape
            for op in tape.operations + tape.measurements:
                if op.name == "CRX":
                    wires = op.wires
                    param = op.parameters[0]

                    qml.RX(x * qml.math.abs(param), wires=wires[1])
                    qml.RY(y * qml.math.abs(param), wires=wires[1])
                    qml.CZ(wires=[wires[1], wires[0]])
                else:
                    op.queue()

    This transform iterates through the input tape, and replaces any :class:`~.CRX` operation with
    two single qubit rotations and a :class:`~.CZ` operation. These newly queued operations will
    form the output transformed tape.

    We can apply this transform to a quantum tape:

    >>> with qml.tape.JacobianTape() as tape:
    ...     qml.Hadamard(wires=0)
    ...     qml.CRX(-0.5, wires=[0, 1])
    >>> new_tape = my_transform(tape, 1., 2.)
    >>> print(new_tape.draw())
     0: ──H───────────────╭Z──┤
     1: ──RX(0.5)──RY(1)──╰C──┤
    """

    def __init__(self, transform_fn):

        if not callable(transform_fn):
            raise ValueError(
                f"The tape transform function to register, {transform_fn}, "
                "does not appear to be a valid Python function or callable."
            )

        self.transform_fn = transform_fn
        functools.update_wrapper(self, transform_fn)

    def __call__(self, tape, *args, **kwargs):
        tape_class = type(tape.__class__.__name__, (NonQueuingTape, tape.__class__), {})

        # new_tape, when first created, is of the class (NonQueuingTape, tape.__class__), so that it
        # doesn't result in a nested tape on the tape
        with tape_class() as new_tape:
            self.transform_fn(tape, *args, **kwargs)

        # Once we're done, revert it back to be simply an instance of tape.__class__.
        new_tape.__class__ = tape.__class__
        return new_tape


def qfunc_transform(tape_transform):
    """Converts a single tape transform to a quantum function (qfunc) transform.

    Args:
        tape_transform (single_tape_transform): the single tape transform
            to convert into the qfunc transform.

    Returns:
        function: A qfunc transform, that acts on any qfunc, and returns a *new*
        qfunc as per the tape transform.

    **Example**

    Given a single tape transform ``my_transform(tape, x, y)``, you can use
    this function to convert it into a qfunc transform:

    >>> my_qfunc_transform = qfunc_transform(my_transform)

    It can then be used to transform an existing qfunc:

    >>> new_qfunc = my_qfunc_transform(0.6, 0.7)(old_qfunc)

    It can also be used as a decorator:

    .. code-block:: python

        @qml.qfunc_transform
        def my_transform(tape, x, y):
            for op in tape.operations + tape.measurements:
                if op.name == "CRX":
                    wires = op.wires
                    param = op.parameters[0]
                    qml.RX(x * param, wires=wires[1])
                    qml.RY(y * qml.math.sqrt(param), wires=wires[1])
                    qml.CZ(wires=[wires[1], wires[0]])
                else:
                    op.queue()

        @my_transform(0.6, 0.1)
        def qfunc(x):
            qml.Hadamard(wires=0)
            qml.CRX(x, wires=[0, 1])

    Let's use this qfunc to create a QNode, so that we can execute it on a quantum
    device:

    >>> dev = qml.device("default.qubit", wires=2)
    >>> qnode = qml.QNode(qfunc, dev)
    >>> print(qml.draw(qnode)(2.5))
     0: ──H───────────────────╭Z──┤
     1: ──RX(1.5)──RY(0.158)──╰C──┤

    Not only is the transformed qfunc fully differentiable, but the qfunc transform
    parameters *themselves* are differentiable:

    .. code-block:: python

        dev = qml.device("default.qubit", wires=2)

        def ansatz(x):
            qml.Hadamard(wires=0)
            qml.CRX(x, wires=[0, 1])

        @qml.qnode(dev)
        def circuit(param, transform_weights):
            qml.RX(0.1, wires=0)

            # apply the transform to the ansatz
            my_transform(*transform_weights)(ansatz)(param)

            return qml.expval(qml.PauliZ(1))

    We can print this QNode to show that the qfunc transform is taking place:

    >>> x = np.array(0.5, requires_grad=True)
    >>> y = np.array([0.1, 0.2], requires_grad=True)
    >>> print(qml.draw(circuit)(x, y))
     0: ──RX(0.1)───H──────────╭Z──┤
     1: ──RX(0.05)──RY(0.141)──╰C──┤ ⟨Z⟩

    Evaluating the QNode, as well as the derivative, with respect to the gate
    parameter *and* the transform weights:

    >>> circuit(x, y)
    0.9887793925354269
    >>> qml.grad(circuit)(x, y)
    (array(-0.02485651), array([-0.02474011, -0.09954244]))
    """
    if not callable(single_tape_transform):
        raise ValueError(
            "The qfunc_transform decorator can only be applied "
            "to single tape transform functions."
        )

    if not isinstance(tape_transform, single_tape_transform):
        tape_transform = single_tape_transform(tape_transform)

    sig = inspect.signature(tape_transform)
    params = sig.parameters

    if len(params) > 1:

        @functools.wraps(tape_transform)
        def make_qfunc_transform(*targs, **tkwargs):
            def wrapper(fn):

                if not callable(fn):
                    raise ValueError(
                        f"The qfunc to transform, {fn}, does not appear "
                        "to be a valid Python function or callable."
                    )

                @functools.wraps(fn)
                def internal_wrapper(*args, **kwargs):
                    tape = make_tape(fn)(*args, **kwargs)
                    tape = tape_transform(tape, *targs, **tkwargs)
                    return tape.measurements

                return internal_wrapper

            return wrapper

    elif len(params) == 1:

        @functools.wraps(tape_transform)
        def make_qfunc_transform(fn):

            if not callable(fn):
                raise ValueError(
                    f"The qfunc to transform, {fn}, does not appear "
                    "to be a valid Python function or callable."
                )

            @functools.wraps(fn)
            def internal_wrapper(*args, **kwargs):
                tape = make_tape(fn)(*args, **kwargs)
                tape = tape_transform(tape)
                return tape.measurements

            return internal_wrapper

    make_qfunc_transform.tape_fn = tape_transform
    return make_qfunc_transform


def qnode_transform(qnode_transform_fn):
    """Register a new QNode transform.

    Args:
        qnode_transform_fn (QNode transform): the QNode transform function
            to register.

            Allowed QNode transforms must be functions of the following form:

            .. code-block:: python

                def qnode_transform(qnode, *args, **kwargs):
                    ...
                    return tapes, processing_fn

            That is, the first argument must be the input QNode to transform,
            and the function must return a tuple ``(list, function)`` containing:

            * A list of new tapes to execute, and
            * A processing function with signature ``processing_fn(List[float])``
              which is applied to the flat list of results from the executed tapes.

            If ``tapes`` is empty, then it is assumed no quantum evaluations
            are required, and ``processing_fn`` will be passed an empty list.

    Returns:
        function: A hybrid quantum-classical function. Takes the same input arguments as
        the input QNode.

    **Example**

    Given a simple tape transform, that replaces the :class:`~.CRX` gate with a
    :class:`~.RY` and :class:`~.CZ` operation,

    .. code-block:: python

        @qml.single_tape_transform
        def tape_transform(tape, x):
            for op in tape.operations + tape.measurements:
                if op.name == "CRX":
                    qml.RY(op.parameters[0] * qml.math.sqrt(x), wires=op.wires[1])
                    qml.CZ(wires=op.wires)
                else:
                    op.queue()

    let's build a QNode transform that applies this transform twice with different
    transform parameters to create two tapes, and then sum the results:

    .. code-block:: python

        @qml.qnode_transform
        def my_transform(qnode, x, y):
            tape1 = tape_transform(qnode.qtape, x)
            tape2 = tape_transform(qnode.qtape, y)

            def processing_fn(results):
                return qml.math.sum(results)

            return [tape1, tape2], processing_fn

    It can then be used to transform an existing QNode:

    .. code-block:: python

        dev = qml.device("default.qubit", wires=2)

        @my_transform(0.7, 0.8)
        @qml.qnode(dev)
        def circuit(x):
            qml.Hadamard(wires=0)
            qml.CRX(x, wires=[0, 1])
            return qml.expval(qml.PauliZ(1))

    >>> circuit(0.6)
    1.7360468658221193

    Not only is the transformed QNode fully differentiable, but the QNode transform
    parameters *themselves* are differentiable:

    .. code-block:: python

        @qml.qnode(dev)
        def circuit(x):
            qml.Hadamard(wires=0)
            qml.CRX(x, wires=[0, 1])
            return qml.expval(qml.PauliZ(1))

        def cost_fn(x, transform_weights):
            transform_fn = my_transform(*transform_weights)(circuit)
            return transform_fn(x)

    Evaluating the transform, as well as the derivative, with respect to the gate
    parameter *and* the transform weights:

    >>> x = np.array(0.6, requires_grad=True)
    >>> transform_weights = np.array([0.7, 0.8], requires_grad=True)
    >>> cost_fn(x, transform_weights)
    1.7360468658221193
    >>> qml.grad(cost_fn)(x, transform_weights)
    (array(-0.85987045), array([-0.17253469, -0.17148357]))
    """
    if not callable(qnode_transform_fn):
        raise ValueError(
            "The qnode_transform decorator can only be applied "
            "to valid Python functions or callables."
        )

    sig = inspect.signature(qnode_transform_fn)
    params = sig.parameters

    if isinstance(qnode_transform_fn, single_tape_transform):
        auto_execute = True
        post_process = False
    else:
        # determine from the return annotation if the QNode transform
        # returns tapes that should be autoexecuted by the decorator
        auto_execute = sig.return_annotation in AUTO_EXECUTE
        post_process = auto_execute and sig.return_annotation in AUTO_EXECUTE_PROCESSING

    if len(params) > 1:

        @functools.wraps(qnode_transform_fn)
        def make_qnode_transform(*targs, **tkwargs):
            def wrapper(qnode):

                if not isinstance(qnode, qml.QNode):
                    raise ValueError(
                        f"The object to transform, {qnode}, does not appear "
                        "to be a valid QNode."
                    )

                @functools.wraps(qnode)
                def internal_wrapper(*args, **kwargs):
                    qnode.construct(args, kwargs)

                    if auto_execute and not post_process:
                        tapes = qnode_transform_fn(qnode.qtape, *targs, **tkwargs)

                        if isinstance(tapes, tuple) and tapes[-1] is None:
                            # quantum function returned a tuple (tapes(s), None)
                            tapes = tapes[0]

                        if not isinstance(tapes, Sequence):
                            # quantum function returned a single tape
                            tapes = [tapes]

                        return [t.execute(device=qnode.device) for t in tapes]

                    if auto_execute:
                        tapes, fn = qnode_transform_fn(qnode, *targs, **tkwargs)
                        res = [t.execute(device=qnode.device) for t in tapes]
                        return fn(res)



                internal_wrapper.qnode = qnode
                internal_wrapper.interface = qnode.interface
                internal_wrapper.device = qnode.device
                return internal_wrapper

            return wrapper

    elif len(params) == 1:

        @functools.wraps(qnode_transform_fn)
        def make_qnode_transform(qnode):

            if not isinstance(qnode, qml.QNode):
                raise ValueError(
                    f"The object to transform, {qnode}, does not appear "
                    "to be a valid QNode."
                )

            @functools.wraps(qnode)
            def internal_wrapper(*args, **kwargs):
                qnode.construct(args, kwargs)

                if isinstance(qnode_transform_fn, single_tape_transform):
                    fn = lambda x: x
                    tapes = [qnode_transform_fn(qnode.qtape)]
                else:
                    tapes, fn = qnode_transform_fn(qnode)

                res = [t.execute(device=qnode.device) for t in tapes]
                return fn(res)

            internal_wrapper.qnode = qnode
            internal_wrapper.interface = qnode.interface
            internal_wrapper.device = qnode.device
            return internal_wrapper

    return make_qnode_transform