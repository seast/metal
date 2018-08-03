import numpy as np
import pickle
from scipy.sparse import issparse, csr_matrix, hstack
import torch
from torch.utils.data import Dataset


class MetalDataset(Dataset):
    """A dataset that group each item in X with it label from Y
    
    Args:
        X: an N-dim iterable of items
        Y: a torch.Tensor of labels
            This may be hard labels [N] or soft labels [N, k]
    """
    def __init__(self, X, Y):
        self.X = X
        self.Y = Y
        assert(len(X) == len(Y))

    def __getitem__(self, index):
        return tuple([self.X[index], self.Y[index]])

    def __len__(self):
        return len(self.X)

def rargmax(x, eps=1e-8):
    """Argmax with random tie-breaking
    
    Args:
        x: a 1-dim numpy array
    Returns:
        the argmax index
    """
    idxs = np.where(abs(x - np.max(x, axis=0)) < eps)[0]
    return np.random.choice(idxs)

def hard_to_soft(Y_h, k):
    """Converts a 1D tensor of hard labels into a 2D tensor of soft labels

    Args:
        Y_h: an [N], or [N,1] tensor of hard (int) labels between 0 and k 
            (inclusive), where 0 = abstain.
        k: the largest possible label in Y_h
    Returns:
        Y_s: a torch.FloatTensor of shape [N, k + 1] where Y_s[i,j] is the soft
            label for item i and class j.
    """
    Y_h = Y_h.clone()
    Y_h = Y_h.squeeze()
    assert(Y_h.dim() == 1)
    assert((Y_h >= 0).all())
    assert((Y_h <= k).all())
    N = Y_h.shape[0]
    Y_s = torch.zeros((N, k+1))
    for i, j in enumerate(Y_h):
        Y_s[i, j] = 1.0
    return Y_s

def arraylike_to_numpy(array_like):
    """Convert a 1d array-like (e.g,. list, tensor, etc.) to an np.ndarray"""

    orig_type = type(array_like)
    
    # Convert to np.ndarray
    if isinstance(array_like, np.ndarray):
        pass
    elif isinstance(array_like, list):
        array_like = np.array(array_like)
    elif issparse(array_like):
        array_like = array_like.toarray()
    elif isinstance(array_like, torch.Tensor):
        array_like = array_like.numpy()
    elif not isinstance(array_like, np.ndarray):
        array_like = np.array(array_like)
    else:
        raise ValueError(f"Input of type {orig_type} could not be converted "
            "to 1d np.ndarray")
        
    # Correct shape
    if (array_like.ndim > 1) and (1 in array_like.shape):
        array_like = array_like.flatten()
    if array_like.ndim != 1:
        raise ValueError("Input could not be converted to 1d np.array")

    # Convert to ints
    if any(array_like % 1):
        raise ValueError("Input contains at least one non-integer value.")
    array_like = array_like.astype(np.dtype(int))

    return array_like

def convert_labels(Y, source, dest):
    """Convert a matrix from one label type to another
    
    Args:
        X: A np.ndarray or torch.Tensor of labels (ints)
        source: The convention the labels are currently expressed in
        dest: The convention to convert the labels to

    Conventions:
        'categorical': [0: abstain, 1: positive, 2: negative]
        'plusminus': [0: abstain, 1: positive, -1: negative]
        'onezero': [0: negative, 1: positive]

    Note that converting to 'onezero' will combine abstain and negative labels.
    """
    if Y is None: return Y
    Y = Y.copy()
    negative_map = {'categorical': 2, 'plusminus': -1, 'onezero': 0}
    Y[Y == negative_map[source]] = negative_map[dest]
    return Y

def plusminus_to_categorical(Y):
    return convert_labels(Y, 'plusminus', 'categorical')

def categorical_to_plusminus(Y):
    return convert_labels(Y, 'categorical', 'plusminus')

def recursive_merge_dicts(x, y, misses='report', verbose=None):
    """
    Merge dictionary y into a copy of x, overwriting elements of x when there 
    is a conflict, except if the element is a dictionary, in which case recurse.

    misses: what to do if a key in y is not in x
        'insert'    -> set x[key] = value
        'exception' -> raise an exception
        'report'    -> report the name of the missing key
        'ignore'    -> do nothing

    TODO: give example here (pull from tests)
    """
    def recurse(x, y, misses='report', verbose=1):
        found = True
        for k, v in y.items():
            found = False
            if k in x:
                found = True
                if isinstance(x[k], dict):
                    if not isinstance(v, dict):
                        msg = (f"Attempted to overwrite dict {k} with "
                            f"non-dict: {v}")
                        raise ValueError(msg)
                    recurse(x[k], v, misses, verbose)
                else:
                    if x[k] == v:
                        msg = f"Reaffirming {k}={x[k]}"
                    else:
                        msg = f"Overwriting {k}={x[k]} to {k}={v}"
                        x[k] = v
                    if verbose > 1 and k != 'verbose':
                        print(msg)
            else:
                for kx, vx in x.items():
                    if isinstance(vx, dict):
                        found = recurse(vx, {k: v}, 
                            misses='ignore', verbose=verbose)
                    if found:
                        break
            if not found:
                msg = f'Could not find kwarg "{k}" in default config.'
                if misses == 'insert':
                    x[k] = v
                    if verbose > 1: 
                        print(f"Added {k}={v} from second dict to first")
                elif misses == 'exception':
                    raise ValueError(msg)
                elif misses == 'report':
                    print(msg)
                else:
                    pass
        return found
    
    # If verbose is not provided, look for an value in y first, then x
    # (Do this because 'verbose' kwarg is often inside one or both of x and y)
    if verbose is None:
        verbose = y.get('verbose', x.get('verbose', 1))

    z = x.copy()
    recurse(z, y, misses, verbose)
    return z

def make_unipolar_matrix(L, force=None):
    """
    Creates a unipolar label matrix from non-unipolar label matrix,
    handles binary and categorical cases
    
    Args:
        csr_matrix L: sparse label matrix
        int force: number of columns into which to force separation
                   of each existing column of L
    
    Outputs:
        csr_matrix L_up: equivalent unipolar matrix
    """
    
    # Creating list of columns for matrix
    col_list = []
    
    for col in range(L.shape[1]):
        # Getting unique values in column, ignoring 0
        col_unique_vals = list(set(L[:,col].data)-set([0]))
        if len(col_unique_vals) == 1 and force is None:
            # If only one unique value in column, keep it
            col_list.append(L[:,col])
        else:
            # Otherwise, make a new column for each value taken by the LF
            if force is not None:
                col_unique_vals = np.arange(1,np.max(force)+1) 
            for val in col_unique_vals:
                # Efficiently creating and appending column for each LF value
                val_col = csr_matrix(np.zeros((L.shape[0],1)))
                val_col = val_col + val*(L[:,col] == val)
                col_list.append(val_col)
                
    # Stacking columns and converting to csr_matrix
    L_up = hstack(col_list)
    L_up = csr_matrix(L_up)
    return L_up

def pickle_model(model, filename):
   """
   Pickles metal model classes

   Args:
        ModelClass model: e.g. metal.LabelModel to be saved
        str filename: name of file to save
    
    Outputs:
        None
   """
    with open(filename,'wb') as fl:
        pickle.dump(model, fl)
        
def unpickle_model(filename):
   """
   Unpickles saved metal model classes

   Args:
        str filename: name of file to load
    
    Outputs:
        ModelClass model: loaded model
   """

    with open(filename,'rb') as fl:
        model = pickle.load(fl)
    return model
