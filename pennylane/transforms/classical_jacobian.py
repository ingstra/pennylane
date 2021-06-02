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
"""
Contains the classical Jacobian transform
"""
# pylint: disable=import-outside-toplevel
import pennylane as qml


def _make_jacobian_fn(fn, interface):
    if interface == "autograd":
        return qml.jacobian(fn)

    if interface == "torch":
        import torch

        def _jacobian(*args, **kwargs):  # pylint: disable=unused-argument
            return torch.autograd.functional.jacobian(fn, args)

        return _jacobian

    if interface == "jax":
        import jax

        return jax.jacobian(fn)

    if interface == "tf":
        import tensorflow as tf

        def _jacobian(*args, **kwargs):
            with tf.GradientTape() as tape:
                tape.watch(args)
                gate_params = fn(*args, **kwargs)

            return tape.jacobian(gate_params, args)

        return _jacobian


class expansion_jacobian:

    def __init__(self, tape, depth=1, stop_at=None):

        self.tape = tape
        self._expanded_tape = None

        def classical_preprocessing(params):
            self.tape.set_parameters(params)
            self._expanded_tape = self.tape.expand(depth=depth, stop_at=stop_at)
            return qml.math.stack(self._expanded_tape.get_parameters())

        self.fn = classical_preprocessing
        self.jac_fn = _make_jacobian_fn(self.fn, self.tape.interface)

    @property
    def expanded_tape(self):
        return self._expanded_tape

    def __call__(self, *args, **kwargs):
        return self.jac_fn(*args, **kwargs)


def classical_jacobian(qnode):
    r"""Returns a function to extract the Jacobian
    matrix of the classical part of a QNode.

    This transform allows the classical dependence between the QNode
    arguments and the quantum gate arguments to be extracted.

    Args:
        qnode (.QNode): QNode to compute the (classical) Jacobian of

    Returns:
        function: Function which accepts the same arguments as the QNode.
        When called, this function will return the Jacobian of the QNode
        gate arguments with respect to the QNode arguments.

    **Example**

    Consider the following QNode:

    >>> @qml.qnode(dev)
    ... def circuit(weights):
    ...     qml.RX(weights[0], wires=0)
    ...     qml.RY(weights[0], wires=1)
    ...     qml.RZ(weights[2] ** 2, wires=1)
    ...     return qml.expval(qml.PauliZ(0))

    We can use this transform to extract the relationship :math:`f: \mathbb{R}^n \rightarrow
    \mathbb{R}^m` between the input QNode arguments :math:`w` and the gate arguments :math:`g`, for
    a given value of the QNode arguments:

    >>> cjac_fn = qml.transforms.classical_jacobian(circuit)
    >>> weights = np.array([1., 1., 1.], requires_grad=True)
    >>> cjac = cjac_fn(weights)
    >>> print(cjac)
    [[1. 0. 0.]
     [1. 0. 0.]
     [0. 0. 2.]]

    The returned Jacobian has rows corresponding to gate arguments, and columns
    corresponding to QNode arguments; that is,

    .. math:: J_{ij} = \frac{\partial}{\partial g_i} f(w_j).

    We can see that:

    - The zeroth element of ``weights`` is repeated on the first two gates generated by the QNode.

    - The second column consisting of all zeros indicates that the generated quantum circuit does
      not depend on the first element of ``weights``.
    """

    def classical_preprocessing(*args, **kwargs):
        """Returns the trainable gate parameters for
        a given QNode input"""
        qnode.construct(args, kwargs)
        return qml.math.stack(qnode.qtape.get_parameters())

    return _make_jacobian_fn(classical_preprocessing, qnode.interface)
