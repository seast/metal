from collections import defaultdict, Counter
from itertools import chain, product
import os

import numpy as np
from numpy.random import random, choice
from scipy.sparse import csc_matrix, lil_matrix
import torch

from metal.metrics import accuracy_score, coverage_score
from metal.multitask.task_graph import TaskHierarchy


def indpm(x, y):
    """Plus-minus indicator function"""
    return 1 if x == y else -1

class SingleTaskTreeDepsGenerator(object):
    """Generates synthetic single-task labels from labeling functions with
    class-conditional accuracies, and class-unconditional pairwise correlations
    forming a tree-structured graph.

    Note that k = the # of true classes; thus source labels are in {0,1,...,k}
    because they include abstains.
    """
    def __init__(self, n, m, k=2, theta_range=(0, 1.5), edge_prob=0.0, 
        theta_edge_range=(-1,1)):
        self.n = n
        self.m = m
        self.k = k

        # Generate correlation structure: edges self.E, parents dict self.parent
        self._generate_edges(edge_prob)

        # Generate class-conditional LF & edge parameters, stored in self.theta
        self._generate_params(theta_range, theta_edge_range)

        # Generate class balance self.p
        self.p = random(self.k)
        self.p /= self.p.sum()

        # Generate the true labels self.Y and label matrix self.L
        self._generate_label_matrix()

        # Compute the conditional clique probabilities
        self._get_conditional_probs()
    
    def _generate_edges(self, edge_prob):
        """Generate a random tree-structured dependency graph based on a
        specified edge probability.
    
        Also create helper data struct mapping child -> parent.
        """
        self.E, self.parent = [], {}
        for i in range(self.m):
            if random() < edge_prob and i > 0:
                p_i = choice(i)
                self.E.append((p_i, i))
                self.parent[i] = p_i

    def _generate_params(self, theta_range, theta_edge_range):
        self.theta = defaultdict(float)
        for i in range(self.m):
            t_min, t_max = min(theta_range), max(theta_range)
            self.theta[i] = (t_max - t_min) * random(self.k+1) + t_min

        # Choose random weights for the edges
        te_min, te_max = min(theta_edge_range), max(theta_edge_range)
        for (i,j) in self.E:
            w_ij = (te_max - te_min) * random() + te_min
            self.theta[(i,j)] = w_ij
            self.theta[(j,i)] = w_ij
    
    def _P(self, i, li, j, lj, y):
        return np.exp( 
            self.theta[i][y] * indpm(li, y) + self.theta[(i,j)] * indpm(li, lj))
    
    def P_conditional(self, i, li, j, lj, y):
        """Compute the conditional probability 
            P_\theta(li | lj, y) 
            = 
            Z^{-1} exp( 
                theta_{i|y} \indpm{ \lambda_i = Y }
                + \theta_{i,j} \indpm{ \lambda_i = \lambda_j }
            )
        In other words, compute the conditional probability that LF i outputs
        li given that LF j output lj, and Y = y, parameterized by
            - a class-conditional LF accuracy parameter \theta_{i|y}
            - a symmetric LF correlation paramter \theta_{i,j}
        """
        Z = np.sum([self._P(i, _li, j, lj, y) for _li in range(self.k+1)])
        return self._P(i, li, j, lj, y) / Z
    
    def _generate_label_matrix(self):
        """Generate an n x m label matrix with entries in {0,...,k}"""
        self.L = np.zeros((self.n, self.m))
        self.Y = np.zeros(self.n, dtype=np.int64)
        for i in range(self.n):
            y = choice(self.k, p=self.p) + 1  # Note that y \in {1,...,k}
            self.Y[i] = y
            for j in range(self.m):
                p_j = self.parent.get(j, 0)
                prob_y = self.P_conditional(j, y, p_j, self.L[i, p_j], y)
                prob_0 = self.P_conditional(j, 0, p_j, self.L[i, p_j], y)
                p = np.ones(self.k+1) * (1 - prob_y - prob_0) / (self.k - 1)
                p[0] = prob_0
                p[y] = prob_y
                self.L[i,j] = choice(self.k+1, p=p)

    def _get_conditional_probs(self):
        """Compute the true clique conditional probabilities P(\lC | Y) by
        counting given L, Y; we'll use this as ground truth to compare to.

        Note that this generates an attribute, self.c_probs, that has the same
        definition as returned by `LabelModel.get_conditional_probs`.

        TODO: Can compute these exactly if we want to implement that.
        """
        # TODO: Extend to higher-order cliques again
        self.c_probs = np.zeros((self.m * (self.k+1), self.k))
        for y in range(1,self.k+1):
            Ly = self.L[self.Y == y]
            for ly in range(self.k+1):
                self.c_probs[ly::(self.k+1), y-1] = \
                    np.where(Ly == ly, 1, 0).sum(axis=0) / Ly.shape[0]


class HierarchicalMultiTaskTreeDepsGenerator(SingleTaskTreeDepsGenerator):
    def __init__(self, n, m, theta_range=(0, 1.5), edge_prob=0.0, 
        theta_edge_range=(-1,1)):
        super().__init__(n, m, k=4, theta_range=theta_range, 
            edge_prob=edge_prob, theta_edge_range=theta_edge_range)

        # Convert label matrix to tree task graph
        self.task_graph = TaskHierarchy(
            edges=[(0,1), (0,2)],
            cardinalities=[2,2,2]
        )
        L_mt = [np.zeros((self.n, self.m)) for _ in range(self.task_graph.t)]
        fs = list(self.task_graph.feasible_set())
        for i in range(self.n):
            for j in range(self.m):
                if self.L[i,j] > 0:
                    y = fs[int(self.L[i,j])-1]
                    for s in range(self.task_graph.t):
                        L_mt[s][i,j] = y[s]
        self.L = list(map(csc_matrix, L_mt))


def gaussian_bags_of_words(Y, vocab, sigma=1, bag_size=[25, 50]):
    """
    Generate Gaussian bags of words based on label assignments

    Args:
        Y: np.array of true labels
        sigma: (float) the standard deviation of the Gaussian distributions
        bag_size: (list) the min and max length of bags of words

    Returns:
        X: (Tensor) a tensor of indices representing tokens
        items: (list) a list of sentences (strings)

    The sentences are conditionally independent, given a label.
    Note that technically we use a half-normal distribution here because we 
        take the absolute value of the normal distribution.

    Example:
        TBD

    """
    def make_distribution(sigma, num_words):
        p = abs(np.random.normal(0, sigma, num_words))
        return p / sum(p)
    
    num_words = len(vocab)
    word_dists = {y: make_distribution(sigma, num_words) for y in set(Y)}
    bag_sizes = np.random.choice(range(min(bag_size), max(bag_size)), len(Y))

    X = []
    items = []
    for i, (y, length) in enumerate(zip(Y, bag_sizes)):
        x = torch.from_numpy(
            np.random.choice(num_words, length, p=word_dists[y]))
        X.append(x)
        items.append(' '.join(vocab[j] for j in x))

    return X, items