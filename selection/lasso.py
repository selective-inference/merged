"""
This module contains a class `lasso`_ that implements
post selection for the lasso
as described in `post selection LASSO`_.


.. _covTest: http://arxiv.org/abs/1301.7161
.. _Kac Rice: http://arxiv.org/abs/1308.3020
.. _Spacings: http://arxiv.org/abs/1401.3889
.. _post selection LASSO: http://arxiv.org/abs/1311.6238


"""
import numpy as np
from sklearn.linear_model import Lasso
from .affine import (constraints, selection_interval,
                     interval_constraints,
                     stack)

from .variance_estimation import (interpolation_estimate,
                                  truncated_estimate)


from scipy.stats import norm as ndist
import warnings

try:
    import cvxpy as cvx
except ImportError:
    warnings.warn('cvx not available')
    pass

DEBUG = False

class lasso(object):

    r"""
    A class for the LASSO for post-selection inference.
    The problem solved is

    .. math::

        \text{minimize}_{\beta} \frac{1}{2n} \|y-X\beta\|^2_2 + 
            f \lambda_{\max} \|\beta\|_1

    where $f$ is `frac` and 

    .. math::

       \lambda_{\max} = \frac{1}{n} \|X^Ty\|_{\infty}

    """

    # level for coverage is 1-alpha
    alpha = 0.05
    UMAU = False

    def __init__(self, y, X, lam, sigma=1):

        self.y = y
        self.X = X
        self.sigma = sigma
        n, p = X.shape
        self.lagrange = lam / n
        self._covariance = self.sigma**2 * np.identity(X.shape[0])

    def fit(self, sklearn_alpha=None, **lasso_args):
        """
        Fit the lasso using `Lasso` from `sklearn`.
        This sets the attribute `soln` and
        forms the constraints necessary for post-selection inference
        by caling `form_constraints()`.

        Parameters
        ----------

        sklearn_alpha : float
            Lagrange parameter, in the normalization set by `sklearn`.

        lasso_args : keyword args
             Passed to `sklearn.linear_model.Lasso`_

        Returns
        -------

        soln : np.float
             Solution to lasso with `sklearn_alpha=self.lagrange`.

        """

        # fit Lasso using scikit-learn 
        clf = Lasso(alpha = self.lagrange, fit_intercept = False)
        clf.fit(self.X, self.y)
        self._soln = beta = clf.coef_       
        self.form_constraints()
        
    def form_constraints(self):
        """
        After having fit lasso, form the constraints
        necessary for inference.

        This sets the attributes: `active_constraints`,
        `inactive_constraints`, `active`.

        Returns
        -------

        None

        """

        # determine equicorrelation set and signs
        beta = self.soln
        n, p = self.X.shape
        lam = self.lagrange * n

        self.active = (beta != 0)
        self.z_E = np.sign(beta[self.active])

        # calculate the "partial correlation" operator R = X_{-E}^T (I - P_E)
        X_E = self.X[:,self.active]
        X_notE = self.X[:,~self.active]
        self._XEinv = np.linalg.pinv(X_E)
        P_E = np.dot(X_E, self._XEinv)
        self.R = np.dot(X_notE.T, np.eye(n)-P_E)

        # inactive constraints
        A0 = np.vstack((self.R, -self.R)) / lam
        b_tmp = np.dot(X_notE.T, np.dot(np.linalg.pinv(X_E.T), self.z_E))
        b0 = np.concatenate((1.-b_tmp, 1.+b_tmp))
        self._inactive_constraints = constraints(A0, b0)
        self._inactive_constraints.covariance *= self.sigma**2

        # active constraints
        C = np.linalg.inv(np.dot(X_E.T, X_E))
        A1 = -np.dot(np.diag(self.z_E), np.dot(C, X_E.T))
        b1 = -lam*np.dot(np.diag(self.z_E), np.dot(C, self.z_E))
        
        self._active_constraints = constraints(A1, b1)
        self._active_constraints.covariance *= self.sigma**2

        self._constraints = stack(self._active_constraints,
                                  self._inactive_constraints)
        self._constraints.covariance *= self.sigma**2
        self.active = np.nonzero(self.active)[0]

    @property
    def soln(self):
        """
        Solution to the lasso problem, set by `fit` method.
        """
        if not hasattr(self, "_soln"):
            self.fit()
        return self._soln

    @property
    def active_constraints(self):
        """
        Affine constraints imposed on the
        active variables by the KKT conditions.
        """
        return self._active_constraints

    @property
    def inactive_constraints(self):
        """
        Affine constraints imposed on the
        inactive subgradient by the KKT conditions.
        """
        return self._inactive_constraints

    @property
    def constraints(self):
        """
        Affine constraints for this LASSO problem.
        This is `self.active_constraints` stacked with
        `self.inactive_constraints`.
        """
        return self._constraints

    @property
    def intervals(self):
        """
        Intervals for OLS parameters of active variables
        adjusted for selection.
        """
        if not hasattr(self, "_intervals"):
            self._intervals = []
            C = self.active_constraints
            XEinv = self._XEinv
            if XEinv is not None:
                for i in range(XEinv.shape[0]):
                    eta = XEinv[i]
                    _interval = C.interval(eta, self.y,
                                           alpha=self.alpha,
                                           UMAU=self.UMAU)
                    self._intervals.append((self.active[i], eta, 
                                            (eta*self.y).sum(), 
                                            _interval))
        return self._intervals

    @property
    def active_pvalues(self, doc="Tests for active variables adjusted " + \
        " for selection."):
        if not hasattr(self, "_pvals"):
            self._pvals = []
            C = self.active_constraints
            XEinv = self._XEinv
            if XEinv is not None:
                for i in range(XEinv.shape[0]):
                    eta = XEinv[i]
                    _pval = C.pivot(eta, self.y)
                    _pval = 2 * min(_pval, 1 - _pval)
                    self._pvals.append((self.active[i], _pval))
        return self._pvals

    @property
    def nominal_intervals(self):
        """
        Intervals for OLS parameters of active variables
        that have not been adjusted for selection.
        """
        if not hasattr(self, "_intervals_unadjusted"):
            if not hasattr(self, "_constraints"):
                self.form_constraints()
            self._intervals_unadjusted = []
            XEinv = self._XEinv
            SigmaE = self.sigma**2 * np.dot(XEinv, XEinv.T)
            for i in range(self.active.shape[0]):
                eta = XEinv[i]
                center = (eta*self.y).sum()
                width = ndist.ppf(1-self.alpha/2.) * np.sqrt(SigmaE[i,i])
                _interval = [center-width, center+width]
                self._intervals_unadjusted.append((self.active[i], eta, (eta*self.y).sum(), 
                                        _interval))
        return self._intervals_unadjusted

def estimate_sigma(y, X, frac=0.1, 
                   lower=0.5,
                   upper=2,
                   npts=15,
                   ndraw=5000,
                   burnin=1000):
    r"""
    Estimate the parameter $\sigma$ in $y \sim N(X\beta, \sigma^2 I)$
    after fitting LASSO with Lagrange parameter `frac` times
    $\lambda_{\max}=\|X^Ty\|_{\infty}$.

    ## FUNCTION NEEDS TO BE UPDATED

    Uses `selection.variance_estimation.interpolation_estimate`

    Parameters
    ----------

    y : np.float
        Response to be used for LASSO.

    X : np.float
        Design matrix to be used for LASSO.

    frac : float
        What fraction of $\lambda_{\max}$ should be used to fit
        LASSO.

    lower : float
        Multiple of naive estimate to use as lower endpoint.

    upper : float
        Multiple of naive estimate to use as upper endpoint.

    npts : int
        Number of points in interpolation grid.

    ndraw : int
        Number of Gibbs steps to use for estimating
        each expectation.

    burnin : int
        How many Gibbs steps to use for burning in.

    Returns
    -------

    sigma_hat : float
        The root of the interpolant derived from GCM values.

    interpolant : scipy.interpolate.interp1d
        The interpolant, to be used for plotting or other 
        diagnostics.

    """

    n, p = X.shape
    L = lasso(y, X, frac=frac)
    soln = L.fit(tol=1.e-14, min_its=200)

    # now form the constraint for the inactive variables

    C = L.inactive_constraints
    PR = np.identity(n) - L.PE
    try:
        U, D, V = np.linalg.svd(PR)
    except np.linalg.LinAlgError:
        D, U = np.linalg.eigh(PR)

    keep = D >= 0.5
    U = U[:,keep]
    Z = np.dot(U.T, y)
    Z_inequality = np.dot(C.inequality, U)
    Z_constraint = constraints(Z_inequality, C.inequality_offset)
    if not Z_constraint(Z):
        raise ValueError('Constraint not satisfied. Gibbs algorithm will fail.')
    return interpolation_estimate(Z, Z_constraint,
                                  lower=lower,
                                  upper=upper,
                                  npts=npts,
                                  ndraw=ndraw,
                                  burnin=burnin,
                                  estimator='simulate')


