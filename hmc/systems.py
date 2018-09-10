"""Classes to represent Hamiltonian systems of various types."""

import warnings
import numpy as np
import numpy.linalg as nla
import scipy.linalg as sla
import scipy.optimize as opt
from hmc.states import cache_in_state, multi_cache_in_state
from hmc.solvers import ConvergenceError
from hmc.utils import maximum_norm

autograd_available = True
try:
    from autograd import grad, value_and_grad, jacobian, hessian, make_vjp
except ImportError:
    autograd_available = False


class HamiltonianSystem(object):
    """Base class for Hamiltonian systems."""

    def __init__(self, pot_energy, val_and_grad_pot_energy=None):
        self._pot_energy = pot_energy
        if val_and_grad_pot_energy is None and autograd_available:
            self._val_and_grad_pot_energy = value_and_grad(pot_energy)
        elif val_and_grad_pot_energy is None and not autograd_available:
            raise ValueError('Autograd not available therefore '
                             'val_and_grad_pot_energy must be provided.')
        else:
            self._val_and_grad_pot_energy = val_and_grad_pot_energy

    @cache_in_state('pos')
    def pot_energy(self, state):
        return self._pot_energy(state.pos)

    @multi_cache_in_state(['pos'], ['pot_energy', 'grad_pot_energy'], 1)
    def grad_pot_energy(self, state):
        return self._val_and_grad_pot_energy(state.pos)

    def h(self, state):
        raise NotImplementedError()

    def dh_dpos(self, state):
        raise NotImplementedError()

    def dh_dmom(self, state):
        raise NotImplementedError()

    def sample_momentum(self, state, rng):
        raise NotImplementedError()


class SeparableHamiltonianSystem(HamiltonianSystem):
    """Base class for separable Hamiltonian systems.

    Here separable means that the Hamiltonian can be expressed as the sum of
    a term depending only on the position (target) variables, typically denoted
    the potential energy, and a second term depending only on the momentum
    variables, typically denoted the kinetic energy.
    """

    @cache_in_state('mom')
    def kin_energy(self, state):
        return self._kin_energy(state.mom)

    @cache_in_state('mom')
    def grad_kin_energy(self, state):
        return self._grad_kin_energy(state.mom)

    def h(self, state):
        return self.pot_energy(state) + self.kin_energy(state)

    def dh_dpos(self, state):
        return self.grad_pot_energy(state)

    def dh_dmom(self, state):
        return self.grad_kin_energy(state)

    def _kin_energy(self, mom):
        raise NotImplementedError()

    def _grad_kin_energy(self, mom):
        raise NotImplementedError()

    def sample_momentum(self, state, rng):
        raise NotImplementedError()


class BaseEuclideanMetricHamiltonianSystem(SeparableHamiltonianSystem):

    def __init__(self, pot_energy, metric=None, val_and_grad_pot_energy=None):
        super().__init__(pot_energy, val_and_grad_pot_energy)
        self.metric = metric

    def mult_inv_metric(self, rhs):
        raise NotImplementedError()

    def mult_metric(self, rhs):
        raise NotImplementedError()


class IsotropicEuclideanMetricHamiltonianSystem(
        BaseEuclideanMetricHamiltonianSystem):
    """Euclidean-Gaussian Hamiltonian system with isotropic metric.

    The momenta are taken to be independent of the position variables and with
    a isotropic covariance zero-mean Gaussian marginal distribution.
    """

    def __init__(self, pot_energy, metric=None, val_and_grad_pot_energy=None):
        super().__init__(pot_energy, 1, val_and_grad_pot_energy)
        if metric is not None:
            warning.warn(
                f'Value of metric is ignored for {type(self).__name__}.')

    def _kin_energy(self, mom):
        return 0.5 * np.sum(mom**2)

    def _grad_kin_energy(self, mom):
        return mom

    def sample_momentum(self, state, rng):
        return rng.normal(size=state.pos.shape)

    def mult_inv_metric(self, rhs):
        return rhs

    def mult_metric(self, rhs):
        return rhs


class DiagonalEuclideanMetricHamiltonianSystem(
        BaseEuclideanMetricHamiltonianSystem):
    """Euclidean-Gaussian Hamiltonian system with diagonal metric.

    The momenta are taken to be independent of the position variables and with
    a zero-mean Gaussian marginal distribution with diagonal covariance matrix.
    """

    def __init__(self, pot_energy, metric, val_and_grad_pot_energy=None):
        super().__init__(pot_energy, metric, val_and_grad_pot_energy)
        if hasattr(metric, 'ndim') and metric.ndim == 2:
            warning.warn(
                f'Off-diagonal metric values ignored for '
                f'{type(self).__name__}.')
            self.metric_diagonal = metric.diagonal()
        else:
            self.metric_diagonal = metric

    def _kin_energy(self, mom):
        return 0.5 * np.sum(mom**2 / self.metric_diagonal)

    def _grad_kin_energy(self, mom):
        return mom / self.metric_diagonal

    def sample_momentum(self, state, rng):
        return self.metric_diagonal**0.5 * rng.normal(size=state.pos.shape)

    def mult_inv_metric(self, rhs):
        return (rhs.T / self.metric_diagonal).T

    def mult_metric(self, rhs):
        return (rhs.T * self.metric_diagonal).T


class DenseEuclideanMetricHamiltonianSystem(
        BaseEuclideanMetricHamiltonianSystem):
    """Euclidean-Gaussian Hamiltonian system with dense metric.

    The momenta are taken to be independent of the position variables and with
    a zero-mean Gaussian marginal distribution with dense covariance matrix.
    """

    def __init__(self, pot_energy, metric, val_and_grad_pot_energy=None):
        super().__init__(pot_energy, metric, val_and_grad_pot_energy)
        self.chol_metric = sla.cholesky(metric, lower=True)

    def _kin_energy(self, mom):
        return 0.5 * mom @ self._grad_kin_energy(mom)

    def _grad_kin_energy(self, mom):
        return sla.cho_solve((self.chol_metric, True), mom)

    def sample_momentum(self, state, rng):
        return self.chol_metric @ rng.normal(size=state.pos.shape)

    def mult_inv_metric(self, rhs):
        return sla.cho_solve((self.chol_metric, True), rhs)

    def mult_metric(self, rhs):
        return self.metric @ rhs


class BaseRiemannianMetricHamiltonianSystem(HamiltonianSystem):

    def sqrt_metric(self, state):
        raise NotImplementedError()

    def log_det_sqrt_metric(self, state):
        raise NotImplementedError()

    def grad_log_det_sqrt_metric(self, state):
        raise NotImplementedError()

    def grad_mom_inv_metric_mom(self, state):
        raise NotImplementedError()

    def inv_metric_mom(self, state):
        raise NotImplementedError()

    def h(self, state):
        return self.h1(state) + self.h2(state)

    def h1(self, state):
        return self.pot_energy(state) + self.log_det_sqrt_metric(state)

    def h2(self, state):
        return 0.5 * state.mom @ self.inv_metric_mom(state)

    def dh1_dpos(self, state):
        return (
            self.grad_pot_energy(state) +
            self.grad_log_det_sqrt_metric(state))

    def dh2_dpos(self, state):
        return 0.5 * self.grad_mom_inv_metric_mom(state)

    def dh_dpos(self, state):
        return self.dh1_dpos(state) + self.dh2_dpos(state)

    def dh_dmom(self, state):
        return self.inv_metric_mom(state)

    def sample_momentum(self, state, rng):
        sqrt_metric = self.sqrt_metric(state)
        return sqrt_metric @ rng.normal(size=state.pos.shape)


class BaseCholeskyRiemannianMetricHamiltonianSystem(
        BaseRiemannianMetricHamiltonianSystem):

    def chol_metric(self, state):
        raise NotImplementedError()

    @cache_in_state('pos')
    def log_det_sqrt_metric(self, state):
        chol_metric = self.chol_metric(state)
        return np.log(chol_metric.diagonal()).sum()

    @cache_in_state('pos', 'mom')
    def inv_metric_mom(self, state):
        chol_metric = self.chol_metric(state)
        return sla.cho_solve((chol_metric, True), state.mom)

    def sqrt_metric(self, state):
        return self.chol_metric(state)


class DenseRiemannianMetricHamiltonianSystem(
            BaseCholeskyRiemannianMetricHamiltonianSystem):

    def __init__(self, pot_energy, metric, grad_pot_energy=None,
                 vjp_and_val_metric=None):
        super().__init__(pot_energy, grad_pot_energy)
        self._metric = metric
        if vjp_and_val_metric is None and autograd_available:
            self._vjp_and_val_metric = make_vjp(metric)
        elif vjp_and_val_metric is None and not autograd_available:
            raise ValueError('Autograd not available therefore '
                             'vjp_and_val_metric must be provided.')
        else:
            self._vjp_and_val_metric = vjp_and_val_metric

    @cache_in_state('pos')
    def grad_log_det_sqrt_metric(self, state):
        inv_metric = self.inv_metric(state)
        return 0.5 * self.vjp_metric(state)(inv_metric)

    @cache_in_state('pos', 'mom')
    def grad_mom_inv_metric_mom(self, state):
        inv_metric_mom = self.inv_metric_mom(state)
        inv_metric_mom_outer = np.outer(inv_metric_mom, inv_metric_mom)
        return -self.vjp_metric(state)(inv_metric_mom_outer)

    @cache_in_state('pos')
    def metric(self, state):
        return self._metric(state.pos)

    @cache_in_state('pos')
    def chol_metric(self, state):
        return sla.cholesky(self.metric(state), True)

    @cache_in_state('pos')
    def inv_metric(self, state):
        chol_metric = self.chol_metric(state)
        return sla.cho_solve((chol_metric, True), np.eye(state.n_dim))

    @multi_cache_in_state(['pos'], ['vjp_metric', 'metric'])
    def vjp_metric(self, state):
        return self._vjp_and_val_metric(state.pos)


class FactoredRiemannianMetricHamiltonianSystem(
            BaseCholeskyRiemannianMetricHamiltonianSystem):

    def __init__(self, pot_energy, chol_metric, grad_pot_energy=None,
                 vjp_and_val_chol_metric=None):
        super().__init__(pot_energy, grad_pot_energy)
        self._chol_metric = chol_metric
        if vjp_and_val_chol_metric is None and autograd_available:
            self._vjp_and_val_chol_metric = make_vjp(chol_metric)
        elif vjp_and_val_chol_metric is None and not autograd_available:
            raise ValueError('Autograd not available therefore '
                             'vjp_and_val_chol_metric must be provided.')
        else:
            self._vjp_and_val_chol_metric = vjp_and_val_chol_metric

    @cache_in_state('pos')
    def grad_log_det_sqrt_metric(self, state):
        inv_chol_metric = self.inv_chol_metric(state)
        return self.vjp_chol_metric(state)(inv_chol_metric.T)

    @cache_in_state('pos', 'mom')
    def grad_mom_inv_metric_mom(self, state):
        chol_metric = self.chol_metric(state)
        inv_chol_metric_mom = sla.solve_triangular(
            chol_metric, state.mom, lower=True)
        inv_metric_mom = self.inv_metric_mom(state)
        inv_metric_mom_outer = np.outer(inv_metric_mom, inv_chol_metric_mom)
        return -2 * self.vjp_chol_metric(state)(inv_metric_mom_outer)

    @cache_in_state('pos')
    def chol_metric(self, state):
        return self._chol_metric(state.pos)

    @cache_in_state('pos')
    def inv_chol_metric(self, state):
        chol_metric = self.chol_metric(state)
        return sla.solve_triangular(
            chol_metric, np.eye(state.n_dim), lower=True)

    @multi_cache_in_state(['pos'], ['vjp_metric', 'metric'])
    def vjp_chol_metric(self, state):
        return self._vjp_and_val_chol_metric(state.pos)


class SoftAbsRiemannianMetricHamiltonianSystem(
            BaseRiemannianMetricHamiltonianSystem):

    def __init__(self, pot_energy, softabs_coeff=1., grad_pot_energy=None,
                 hess_pot_energy=None, vjp_hess_pot_energy=None):
        super().__init__(pot_energy, grad_pot_energy)
        self.softabs_coeff = softabs_coeff
        if hess_pot_energy is None and autograd_available:
            self._hess_pot_energy = hessian(pot_energy)
        elif hess_pot_energy is None and not autograd_available:
            raise ValueError('Autograd not available therefore hess_pot_energy'
                             ' must be provided.')
        else:
            self._hess_pot_energy = hess_pot_energy
        if vjp_hess_pot_energy is None and autograd_available:
            self._vjp_hess_pot_energy = make_vjp(self._hess_pot_energy)
        elif vjp_hess_pot_energy is None and not autograd_available:
            raise ValueError('Autograd not available therefore '
                             'vjp_hess_pot_energy must be provided.')
        else:
            self._vjp_hess_pot_energy = vjp_hess_pot_energy

    def softabs(self, x):
        return x / np.tanh(x * self.softabs_coeff)

    def grad_softabs(self, x):
        return (
            1. / np.tanh(self.softabs_coeff * x) -
            self.softabs_coeff * x / np.sinh(self.softabs_coeff * x)**2)

    @cache_in_state('pos')
    def hess_pot_energy(self, state):
        return self._hess_pot_energy(state.pos)

    @cache_in_state('pos')
    def vjp_hess_pot_energy(self, state):
        return self._vjp_hess_pot_energy(state.pos)[0]

    @cache_in_state('pos')
    def eig_metric(self, state):
        hess = self.hess_pot_energy(state)
        hess_eigval, eigvec = sla.eigh(hess)
        metric_eigval = self.softabs(hess_eigval)
        return metric_eigval, hess_eigval, eigvec

    @cache_in_state('pos')
    def sqrt_metric(self, state):
        metric_eigval, hess_eigval, eigvec = self.eig_metric(state)
        return eigvec * metric_eigval**0.5

    @cache_in_state('pos')
    def log_det_sqrt_metric(self, state):
        metric_eigval, hess_eigval, eigvec = self.eig_metric(state)
        return 0.5 * np.log(metric_eigval).sum()

    @cache_in_state('pos')
    def grad_log_det_sqrt_metric(self, state):
        metric_eigval, hess_eigval, eigvec = self.eig_metric(state)
        return 0.5 * self.vjp_hess_pot_energy(state)(
            eigvec * self.grad_softabs(hess_eigval) / metric_eigval @ eigvec.T)

    @cache_in_state('pos', 'mom')
    def inv_metric_mom(self, state):
        metric_eigval, hess_eigval, eigvec = self.eig_metric(state)
        return (eigvec / metric_eigval) @ (eigvec.T @ state.mom)

    @cache_in_state('pos', 'mom')
    def grad_mom_inv_metric_mom(self, state):
        metric_eigval, hess_eigval, eigvec = self.eig_metric(state)
        num_j_mtx = metric_eigval[:, None] - metric_eigval[None, :]
        num_j_mtx += np.diag(self.grad_softabs(hess_eigval))
        den_j_mtx = hess_eigval[:, None] - hess_eigval[None, :]
        np.fill_diagonal(den_j_mtx, 1)
        j_mtx = num_j_mtx / den_j_mtx
        eigvec_mom = (eigvec.T @ state.mom) / metric_eigval
        return -self.vjp_hess_pot_energy(state)(
            eigvec @ (np.outer(eigvec_mom, eigvec_mom) * j_mtx) @ eigvec.T)