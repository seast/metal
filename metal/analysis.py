from collections import Counter, defaultdict

import matplotlib.pyplot as plt
import numpy as np
from pandas import Series, DataFrame
import scipy.sparse as sparse

from metal.utils import arraylike_to_numpy

def error_buckets(gold, pred, X=None):
    """Group items by error buckets
    
    Args:
        gold: an array-like of gold labels (ints)
        pred: an array-like of predictions (ints)
        X: an iterable of items
    Returns:
        buckets: A dict of items where buckets[i,j] is a list of items with
            predicted label i and true label j. If X is None, return indices
            instead.

    For a binary problem with (1=positive, 2=negative):
        buckets[1,1] = true positives
        buckets[1,2] = false positives
        buckets[2,1] = false negatives
        buckets[2,2] = true negatives
    """
    buckets = defaultdict(list)
    gold = arraylike_to_numpy(gold)
    pred = arraylike_to_numpy(pred)    
    for i, (y, l) in enumerate(zip(gold, pred)):
        buckets[y,l].append(X[i] if X is not None else i)
    return buckets


def confusion_matrix(gold, pred, null_pred=False, null_gold=False, 
    normalize=False, pretty_print=True):
    """A shortcut method for building a confusion matrix all at once.
    
    Args:
        gold: an array-like of gold labels (ints)
        pred: an array-like of predictions (ints)
        null_pred: If True, include the row corresponding to null predictions
        null_gold: If True, include the col corresponding to null gold labels
        normalize: if True, divide counts by the total number of items
        pretty_print: if True, pretty-print the matrix before returning
    """    
    conf = ConfusionMatrix(null_pred=null_pred, null_gold=null_gold)
    gold = arraylike_to_numpy(gold)
    pred = arraylike_to_numpy(pred)
    conf.add(gold, pred)
    mat = conf.compile()
    
    if normalize:
        mat = mat / len(gold)

    if pretty_print:
        conf.display()

    return mat

class ConfusionMatrix(object):
    """
    An iteratively built abstention-aware confusion matrix with pretty printing

    Assumed axes are true label on top, predictions on the side.
    """
    def __init__(self, null_pred=False, null_gold=False):
        """
        Args:
            null_pred: If True, include the row corresponding to null predictions
            null_gold: If True, include the col corresponding to null gold labels

        """
        self.counter = Counter()
        self.mat = None
        self.null_pred = null_pred
        self.null_gold = null_gold

    def __repr__(self):
        if self.mat is None:
            self.compile()
        return str(self.mat)

    def add(self, gold, pred):
        """
        Args:
            gold: a np.ndarray of gold labels (ints)
            pred: a np.ndarray of predictions (ints)
        """
        self.counter.update(zip(gold, pred))
    
    def compile(self, trim=True):
        k = max([max(tup) for tup in self.counter.keys()]) + 1  # include 0

        mat = np.zeros((k, k), dtype=int)
        for (p, y), v in self.counter.items():
            mat[p, y] = v
        
        if trim and not self.null_pred:
            mat = mat[1:, :]
        if trim and not self.null_gold:
            mat = mat[:, 1:]

        self.mat = mat
        return mat

    def display(self, counts=True, indent=0, spacing=2, decimals=3, 
        mark_diag=True):
        mat = self.compile(trim=False)
        m, n = mat.shape
        tab = ' ' * spacing
        margin = ' ' * indent

        # Print headers
        s = margin + ' ' * (5 + spacing)
        for j in range(n):
            if j == 0 and not self.null_gold:
                continue
            s += f" y={j} " + tab
        print(s)

        # Print data
        for i in range(m):
            # Skip null predictions row if necessary
            if i == 0 and not self.null_pred:
                continue
            s = margin + f" l={i} " + tab
            for j in range(n):
                # Skip null gold if necessary
                if j == 0 and not self.null_gold:
                    continue
                else:
                    if i == j and mark_diag and not counts:
                        s = s[:-1] + '*'
                    if counts:
                        s += f"{mat[i,j]:^5d}" + tab
                    else:
                        s += f"{mat[i,j]/sum(mat[i,1:]):>5.3f}" + tab
            print(s)

############################################################
# Label Matrix Diagnostics
############################################################
def _covered_data_points(L):
    """Returns an indicator vector where ith element = 1 if x_i is labeled by at
    least one LF."""
    return np.ravel(np.where(L.sum(axis=1) != 0, 1, 0))

def _overlapped_data_points(L):
    """Returns an indicator vector where ith element = 1 if x_i is labeled by 
    more than one LF."""
    return np.where(np.ravel((L != 0).sum(axis=1)) > 1, 1, 0)

def _conflicted_data_points(L):
    """Returns an indicator vector where ith element = 1 if x_i is labeled by 
    at least two LFs that give it disagreeing labels."""
    M = sparse.diags(np.ravel(L.max(axis=1).todense()))
    return np.ravel(np.max(M @ (L != 0) != L, axis=1).astype(int).todense())

def label_coverage(L):
    """Returns the **fraction of data points with > 0 (non-zero) labels**
    Args:
        L: an N x M scipy.sparse matrix where L_{i,j} is the label given by the 
            jth LF to the ith item
    """
    return _covered_data_points(L).sum() / L.shape[0]

def label_overlap(L):
    """Returns the **fraction of data points with > 1 (non-zero) labels**
    Args:
        L: an N x M scipy.sparse matrix where L_{i,j} is the label given by the 
            jth LF to the ith item
    """
    return _overlapped_data_points(L).sum() / L.shape[0]

def label_conflict(L):
    """Returns the **fraction of data points with conflicting (disagreeing)
    lablels.**
    Args:
        L: an N x M scipy.sparse matrix where L_{i,j} is the label given by the 
            jth LF to the ith item
    """
    return _conflicted_data_points(L).sum() / L.shape[0]

def lf_polarities(L):
    """Return the polarities of each LF based on evidence in a label matrix.
    
    Args:
        L: an N x M scipy.sparse matrix where L_{i,j} is the label given by the 
            jth LF to the ith candidate
    """
    polarities = [sorted(list(set(L[:,i].data))) for i in range(L.shape[1])]
    return [p[0] if len(p) == 1 else p for p in polarities]

def lf_coverages(L):
    """Return the **fraction of data points that each LF labels.**
    Args:
        L: an N x M scipy.sparse matrix where L_{i,j} is the label given by the 
            jth LF to the ith candidate
    """
    return np.ravel((L != 0).sum(axis=0)) / L.shape[0]

def lf_overlaps(L, normalize_by_coverage=False):
    """Return the **fraction of items each LF labels that are also labeled by at
     least one other LF.**
    
    Note that the maximum possible overlap fraction for an LF is the LF's
    coverage, unless `normalize_by_coverage=True`, in which case it is 1.

    Args:
        L: an N x M scipy.sparse matrix where L_{i,j} is the label given by the 
            jth LF to the ith candidate
        normalize_by_coverage: Normalize by coverage of the LF, so that it 
            returns the percent of LF labels that have overlaps.
    """    
    overlaps = (L != 0).T @ _overlapped_data_points(L) / L.shape[0]
    if normalize_by_coverage:
        overlaps /= lf_coverages(L)
    return np.nan_to_num(overlaps)

def lf_conflicts(L, normalize_by_overlaps=False):
    """Return the **fraction of items each LF labels that are also given a 
    different (non-abstain) label by at least one other LF.**

    Note that the maximum possible conflict fraction for an LF is the LF's
        overlaps fraction, unless `normalize_by_overlaps=True`, in which case it
        is 1.
    
    Args:
        L: an N x M scipy.sparse matrix where L_{i,j} is the label given by the 
            jth LF to the ith candidate
        normalize_by_overlaps: Normalize by overlaps of the LF, so that it 
            returns the percent of LF overlaps that have conflicts.
    """
    conflicts = (L != 0).T @ _conflicted_data_points(L) / L.shape[0]
    if normalize_by_overlaps:
        conflicts /= lf_overlaps(L)
    return np.nan_to_num(conflicts)

def lf_empirical_accuracies(L, Y):
    """Return the **empirical accuracy** against a set of labels Y (e.g. dev 
    set) for each LF.
    Args:
        L: an N x M scipy.sparse matrix where L_{i,j} is the label given by the 
            jth LF to the ith candidate
        Y: an [N] or [N, 1] np.ndarray of gold labels
    """
    # Assume labeled set is small, work with dense matrices  
    Y = arraylike_to_numpy(Y)
    L = L.toarray()
    X = np.where(L == 0, 0,
        np.where(L == np.vstack([Y] * L.shape[1]).T, 1, -1))
    return 0.5 * (X.sum(axis=0) / (L != 0).sum(axis=0) + 1)

def lf_summary(L, Y=None, lf_names=None, est_accs=None):
    """Returns a pandas DataFrame with the various per-LF statistics.
    
    Args:
        L: an N x M scipy.sparse matrix where L_{i,j} is the label given by the 
            jth LF to the ith candidate
        Y: an [N] or [N, 1] np.ndarray of gold labels
    """
    n, m = L.shape
    if lf_names is not None:
        col_names = ['j']
        d = {'j': list(range(m))}
    else:
        lf_names = list(range(m))
        col_names = []
        d = {}

    # Default LF stats
    col_names.extend(['Polarity', 'Coverage', 'Overlaps', 'Conflicts'])
    d['Polarity']  = Series(data=lf_polarities(L), index=lf_names)
    d['Coverage']  = Series(data=lf_coverages(L), index=lf_names)
    d['Overlaps']  = Series(data=lf_overlaps(L), index=lf_names)
    d['Conflicts'] = Series(data=lf_conflicts(L), index=lf_names)

    if Y is not None:
        col_names.extend(['Correct', 'Incorrect', 'Emp. Acc.'])
        confusions = [confusion_matrix(Y, L[:,i], pretty_print=False) 
            for i in range(m)]
        corrects = [np.diagonal(conf).sum() for conf in confusions]
        incorrects = [conf.sum() - correct for conf, correct in 
            zip(confusions, corrects)]
        accs = lf_empirical_accuracies(L, Y)
        d['Correct']   = Series(data=corrects, index=lf_names)
        d['Incorrect'] = Series(data=incorrects, index=lf_names)
        d['Emp. Acc.'] = Series(data=accs, index=lf_names)

    if est_accs is not None:
        col_names.append('Learned Acc.')
        d['Learned Acc.'] = Series(est_accs, index=lf_names)

    return DataFrame(data=d, index=lf_names)[col_names]    

def single_lf_summary(Y_p, Y=None):
    """Calculates coverage, overlap, conflicts, and accuracy for a single LF

    Args:
        Y_p: a np.array or torch.Tensor of predicted labels
        Y: a np.array or torch.Tensor of true labels (if known)
    """
    L = sparse.csr_matrix(arraylike_to_numpy(Y_p).reshape(-1,1))
    return lf_summary(L, Y)

############################################################
# Label Matrix Plotting
############################################################
def plot_probabilities_histogram(Y_p, title=None):
    """Plot a histogram from a numpy array of probabilities
    
    Args:
        Y_p: An [N] or [N, 1] np.ndarray of probabilities (floats in [0,1])
    """
    plt.hist(Y_p, bins=20)
    plt.xlim((0, 1.025))
    plt.xlabel("Probability")
    plt.ylabel("# Predictions")
    if isinstance(title, str):
        plt.title(title)
    plt.show()

def plot_predictions_histogram(Y_ph, Y, title=None):
    """Plot a histogram comparing hard predictions vs true labels by class
    
    Args:
        Y_ph: An [N] or [N, 1] np.ndarray of predicted hard labels
        Y: An [N] or [N, 1] np.ndarray of gold labels 
    """
    labels = list(set(Y).union(set(Y_ph)))
    edges = [x - 0.5 for x in range(min(labels), max(labels) + 2)]

    plt.hist(
        [Y_ph, Y], 
        bins=edges,
        label=['Predicted', 'Gold'],
    )
    ax = plt.gca()
    ax.set_xticks(labels)
    plt.xlabel("Label")
    plt.ylabel("# Predictions")
    plt.legend(loc='upper right')
    if isinstance(title, str):
        plt.title(title)
    plt.show()

def view_label_matrix(L, colorbar=True):
    """Display an [N, M] matrix of labels"""
    L = L.todense() if sparse.issparse(L) else L
    plt.imshow(L, aspect='auto')
    plt.title("Label Matrix")
    if colorbar:
        labels = sorted(np.unique(np.asarray(L).reshape(-1,1).squeeze()))
        boundaries = np.array(labels + [max(labels) + 1]) - 0.5
        plt.colorbar(boundaries=boundaries, ticks=labels)

def view_overlaps(L, self_overlaps=False, normalize=True, colorbar=True):
    """Display an [M, M] matrix of overlaps"""
    L = L.todense() if sparse.issparse(L) else L
    G = _get_overlaps_matrix(L, normalize=normalize)
    if not self_overlaps:
        np.fill_diagonal(G, 0) # Zero out self-overlaps
    plt.imshow(G, aspect='auto')
    plt.title("Overlaps")
    if colorbar:
        plt.colorbar()

def view_conflicts(L, normalize=True, colorbar=True):
    """Display an [M, M] matrix of conflicts"""
    L = L.todense() if sparse.issparse(L) else L
    C = _get_conflicts_matrix(L, normalize=normalize)
    plt.imshow(C, aspect='auto')
    plt.title("Conflicts")
    if colorbar:
        plt.colorbar()

def _get_overlaps_matrix(L, normalize=True):
    n, m = L.shape
    X = np.where(L != 0, 1, 0).T
    G = X @ X.T 
    
    if normalize:
        G = G / n
    return G

def _get_conflicts_matrix(L, normalize=True):
    n, m = L.shape
    C = np.zeros((m, m))

    # Iterate over the pairs of LFs
    for i in range(m):
        for j in range(m):
            # Get the overlapping non-zero indices
            overlaps = list(set(np.where(L[:,i] != 0)[0]).intersection(np.where(L[:,j] != 0)[0]))
            C[i,j] = np.where(L[overlaps,i] != L[overlaps,j], 1, 0).sum()
            
    if normalize:
        C = C / n
    return C

