from itertools import product, chain

import numpy as np
from scipy.sparse import issparse, csc_matrix
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

from metal.analysis import (
    plot_probabilities_histogram,
    lf_summary,
    confusion_matrix,
)
from metal.classifier import Classifier
from metal.label_model.lm_defaults import lm_model_defaults
from metal.utils import recursive_merge_dicts
from metal.label_model.graph_utils import get_clique_tree


class LabelModel(Classifier):
    def __init__(self, m, k=2, task_graph=None, p=None, deps=[], **kwargs):
        """
        Args:
            m: int: Number of sources
            k: int: Number of true classes
            task_graph: TaskGraph: A TaskGraph which defines a feasible set of
                task label vectors; note this overrides k
            p: np.array: Class balance
            deps: list: A list of source dependencies as tuples of indices 
            kwargs:
                - seed: int: Random state seed
        """
        self.config = recursive_merge_dicts(lm_model_defaults, kwargs)
        super().__init__()
        self.k = k
        self.m = m

        # TaskGraph; note overrides k if present
        self.task_graph = task_graph
        if self.task_graph is not None:
            self.k = len(self.task_graph)
        self.multi_task = (self.task_graph is not None)

        # Class balance- assume uniform if not provided
        if p is None:
            self.p = (1/self.k) * np.ones(self.k)
        else:
            self.p = p
        self.P = torch.diag(torch.from_numpy(self.p)).float()
        
        # Dependencies
        self.deps = deps
        self.c_tree = get_clique_tree(range(self.m), self.deps)

        # Whether to take the simple conditionally independent approach, or the
        # "inverse form" approach for handling dependencies
        # This flag allows us to eg test the latter even with no deps present
        self.inv_form = (len(self.deps) > 0)
    
    def update_config(self, update_dict):
        """Updates self.config with the values in a given update dictionary"""
        self.config = recursive_merge_dicts(self.config, update_dict)
    
    def _get_augmented_label_matrix(self, L, offset=1, higher_order=False):
        """Returns an augmented version of L where each column is an indicator
        for whether a certain source or clique of sources voted in a certain
        pattern.
        
        Args:
            - L: A dense n x m numpy array, where n is the number of data points
                and m is the number of sources, with values in {0,1,...,k}
            - offset: Create indicators for values {offset,...,k}
        """
        # TODO: Handle in cleaner way
        if self.multi_task:
            t = len(L)
            n, m = L[0].shape
        else:
            t = 1
            n, m = L.shape
        km = self.k + 1 - offset

        # Create a helper data structure which maps cliques (as tuples of member
        # sources) --> {start_index, end_index, maximal_cliques}, where
        # the last value is a set of indices in this data structure
        self.c_data = {}
        for i in range(m):
            self.c_data[i] = {
                'start_index': i*km,
                'end_index': (i+1)*km,
                'max_cliques': set([j for j in self.c_tree.nodes() 
                    if i in self.c_tree.node[j]['members']])
            }

        # Form the columns corresponding to unary source labels
        if self.multi_task:
            L_aug = np.ones((n, m * km))

            # TODO: By default, this will operate with offset = 1 by skipping
            # abstains; should fix this!
            for yi, y in enumerate(self.task_graph.feasible_set()):
                for s in range(t):
                    # Note that we cast to dense here, and are creating a dense
                    # matrix; can change to fully sparse operations if needed
                    L_s = L[s].todense()
                    L_aug[:, yi::km] *= np.where(
                        np.logical_or(L_s == y[s], L_s == 0), 1, 0)
                
                # Handle abstains- if all elements of the task label are 0
                L_aug[:, yi::km] *= np.where(
                    sum(map(abs, L)).todense() != 0, 1, 0)

        else:
            L_aug = np.zeros((n, m * km))
            for y in range(offset, self.k+1):
                L_aug[:, y-offset::km] = np.where(L == y, 1, 0)
        
        # Get the higher-order clique statistics based on the clique tree
        # First, iterate over the maximal cliques (nodes of c_tree) and
        # separator sets (edges of c_tree)
        if higher_order:
            for item in chain(self.c_tree.nodes(), self.c_tree.edges()):
                if isinstance(item, int):
                    C = self.c_tree.node[item]
                    C_type = 'node'
                elif isinstance(item, tuple):
                    C = self.c_tree[item[0]][item[1]]
                    C_type = 'edge'
                else:
                    raise ValueError(item)
                members = list(C['members'])
                nc = len(members)

                # If a unary maximal clique, just store its existing index
                if nc == 1:
                    C['start_index'] = members[0] * km
                    C['end_index'] = (members[0]+1) * km
                
                # Else add one column for each possible value
                else:
                    L_C = np.ones((n, km ** nc))
                    for i, vals in enumerate(product(range(km), repeat=nc)):
                        for j, v in enumerate(vals):
                            L_C[:,i] *= L_aug[:, members[j]*km + v]

                    # Add to L_aug and store the indices
                    C['start_index'] = L_aug.shape[1]
                    C['end_index'] = L_aug.shape[1] + L_C.shape[1]
                    L_aug = np.hstack([L_aug, L_C])
                
                # Add to self.c_data as well
                self.c_data[tuple(members)] = {
                    'start_index': C['start_index'],
                    'end_index': C['end_index'],
                    'max_cliques': set([item]) if C_type=='node' else set(item)
                }
        return L_aug
    
    def _generate_O(self, L):
        """Form the overlaps matrix, which is just all the different observed
        combinations of values of pairs of sources

        Note that we only include the k non-abstain values of each source,
        otherwise the model not minimal --> leads to singular matrix
        """
        # TODO: Handle in cleaner way
        if self.multi_task:
            self.t = len(L)
            self.n, self.m = L[0].shape
        else:
            self.t = 1
            self.n, self.m = L.shape
        L_aug = self._get_augmented_label_matrix(L, offset=1)
        self.d = L_aug.shape[1]
        self.O = torch.from_numpy( L_aug.T @ L_aug / self.n ).float()
    
    def _generate_O_inv(self, L):
        """Form the *inverse* overlaps matrix"""
        self._generate_O(L)
        self.O_inv = torch.from_numpy(np.linalg.inv(self.O.numpy())).float()
    
    def _init_params(self):
        """Initialize the learned params
        
        - \mu is the primary learned parameter, where each row corresponds to 
        the probability of a clique C emitting a specific combination of labels,
        conditioned on different values of Y (for each column); that is:
        
            self.mu[i*self.k + j, y] = P(\lambda_i = j | Y = y)
        
        and similarly for higher-order cliques.
        - Z is the inverse form version of \mu.
        - mask is the mask applied to O^{-1}, O for the matrix approx constraint
        """
        # Initialize mu so as to break basic reflective symmetry
        # TODO: Update for higher-order cliques!
        self.mu_init = torch.zeros(self.d, self.k)
        for i in range(self.m):
            for y in range(self.k):
                self.mu_init[i*self.k + y, y] += np.random.random()
        self.mu = nn.Parameter(self.mu_init.clone()).float()

        if self.inv_form:
            self.Z = nn.Parameter(torch.randn(self.d, self.k)).float()

        self.mask = torch.ones(self.d, self.d).byte()
        for ci in self.c_data.values():
            si, ei = ci['start_index'], ci['end_index']
            for cj in self.c_data.values():
                sj, ej = cj['start_index'], cj['end_index']

                # Check if ci and cj are part of the same maximal clique
                # If so, mask out their corresponding blocks in O^{-1}
                if len(ci['max_cliques'].intersection(cj['max_cliques'])) > 0:
                    self.mask[si:ei, sj:ej] = 0
                    self.mask[sj:ej, si:ei] = 0
    
    def get_conditional_probs(self, source=None):
        """Returns the full conditional probabilities table as a numpy array,
        where row i*(k+1) + ly is the conditional probabilities of source i 
        emmiting label ly (including abstains 0), conditioned on different 
        values of Y, i.e.:
        
            c_probs[i*(k+1) + ly, y] = P(\lambda_i = ly | Y = y)
        
        Note that this simply involves inferring the kth row by law of total
        probability and adding in to mu.
        
        If `source` is not None, returns only the corresponding block.
        """
        c_probs = np.zeros((self.m * (self.k+1), self.k))
        mu = self.mu.detach().clone().numpy()
        
        for i in range(self.m):
            # si = self.c_data[(i,)]['start_index']
            # ei = self.c_data[(i,)]['end_index']
            # mu_i = mu[si:ei, :]
            mu_i = mu[i*self.k:(i+1)*self.k, :]
            c_probs[i*(self.k+1) + 1:(i+1)*(self.k+1), :] = mu_i 
            
            # The 0th row (corresponding to abstains) is the difference between
            # the sums of the other rows and one, by law of total prob
            c_probs[i*(self.k+1), :] = 1 - mu_i.sum(axis=0)
        c_probs = np.clip(c_probs, 0.01, 0.99)
    
        if source is not None:
            return c_probs[source*(self.k+1):(source+1)*(self.k+1)]
        else:
            return c_probs

    def predict_proba(self, L):
        return self.get_label_probs(L)

    def get_label_probs(self, L):
        """Returns the n x k matrix of label probabilities P(Y | \lambda)"""
        L_aug = self._get_augmented_label_matrix(L, offset=1)        
        mu = np.clip(self.mu.detach().clone().numpy(), 0.01, 0.99)

        # Create a "junction tree mask" over the columns of L_aug / mu
        if len(self.deps) > 0:
            jtm = np.zeros(L_aug.shape[1])

            # All maximal cliques are +1
            for i in self.c_tree.nodes():
                node = self.c_tree.node[i]
                jtm[node['start_index']:node['end_index']] = 1

            # All separator sets are -1
            for i, j in self.c_tree.edges():
                edge = self.c_tree[i][j]
                jtm[edge['start_index']:edge['end_index']] = 1
        else:
            jtm = np.ones(L_aug.shape[1])

        # Note: We omit abstains, effectively assuming uniform distribution here
        X = np.exp( L_aug @ np.diag(jtm) @ np.log(mu) + np.log(self.p) )
        Z = np.tile(X.sum(axis=1).reshape(-1,1), self.k)
        return X / Z

    def loss_inv_Z(self, l2=0.0):
        return torch.norm((self.O_inv + self.Z @ self.Z.t())[self.mask])**2
    
    def get_Q(self):
        """Get the model's estimate of Q = \mu P \mu^T
        
        We can then separately extract \mu subject to additional constraints,
        e.g. \mu P 1 = diag(O).
        """
        Z = self.Z.detach().clone().numpy()
        O = self.O.numpy()
        I_k = np.eye(self.k)
        return O @ Z @ np.linalg.inv(I_k + Z.T @ O @ Z) @ Z.T @ O

    def loss_inv_mu(self, l2=0.0):
        loss_1 = torch.norm(self.Q - self.mu @ self.P @ self.mu.t())**2
        loss_2 = torch.norm(
            torch.sum(self.mu @ self.P, 1) - torch.diag(self.O))**2
        return loss_1 + loss_2
    
    def loss_mu(self, l2=0.0):
        loss_1 = torch.norm(
            (self.O - self.mu @ self.P @ self.mu.t())[self.mask])**2
        loss_2 = torch.norm(
            torch.sum(self.mu @ self.P, 1) - torch.diag(self.O))**2
        # loss_l2 = torch.norm( self.mu - self.mu_init )**2
        loss_l2 = 0
        return loss_1 + loss_2 + l2 * loss_l2
    
    def train(self, L, **kwargs):
        """Train the model (i.e. estimate mu) in one of two ways, depending on
        whether source dependencies are provided or not:
        
        (1) No dependencies (conditionally independent sources): Estimate mu
        subject to constraints:
            (1a) O_{B(i,j)} - (mu P mu.T)_{B(i,j)} = 0, for i != j, where B(i,j)
                is the block of entries corresponding to sources i,j
            (1b) np.sum( mu P, 1 ) = diag(O)
        
        (2) Source dependencies:
            - First, estimate Z subject to the inverse form
            constraint:
                (2a) O_\Omega + (ZZ.T)_\Omega = 0, \Omega is the deps mask
            - Then, compute Q = mu P mu.T
            - Finally, estimate mu subject to mu P mu.T = Q and (1b)
        """
        self.config = recursive_merge_dicts(self.config, kwargs, 
            misses='ignore')

        if self.inv_form:
            # Compute O, O^{-1}, and initialize params
            if self.config['verbose']:
                print("Computing O^{-1}...")
            self._generate_O_inv(L)
            self._init_params()

            # Estimate Z, compute Q = \mu P \mu^T
            if self.config['verbose']:
                print("Estimating Z...")
            self._train(self.loss_inv_Z)
            self.Q = torch.from_numpy(self.get_Q()).float()

            # Estimate \mu
            if self.config['verbose']:
                print("Estimating \mu...")
            self._train(self.loss_inv_mu)
        else:
            # Compute O and initialize params
            if self.config['verbose']:
                print("Computing O...")
            self._generate_O(L)
            self._init_params()

            # Estimate \mu
            if self.config['verbose']:
                print("Estimating \mu...")
            self._train(self.loss_mu)

    def _train(self, loss_fn):
        """Train model (self.parameters()) by optimizing the provided loss fn"""
        train_config = self.config['train_config']

        # Set optimizer as SGD w/ momentum
        optimizer_config = self.config['train_config']['optimizer_config']
        optimizer = optim.SGD(
            self.parameters(),
            **optimizer_config['optimizer_common'],
            **optimizer_config['sgd_config']
        )

        # Train model
        for epoch in range(train_config['n_epochs']):
            optimizer.zero_grad()
            
            # Compute gradient and take a step
            # Note that since this uses all N training points this is an epoch!
            loss = loss_fn(l2=train_config['l2'])
            if torch.isnan(loss):
                raise Exception("Loss is NaN. Consider reducing learning rate.")

            loss.backward()
            optimizer.step()
            
            # Print loss every print_every steps
            if (self.config['verbose'] and 
                (epoch % train_config['print_every'] == 0 
                or epoch == train_config['n_epochs'] - 1)):
                msg = f"[Epoch {epoch}] Loss: {loss.item():0.6f}"
                print(msg)