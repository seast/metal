import sys
import unittest

import numpy as np
import torch

from metal.input_modules import LSTMModule
from metal.end_model import EndModel


N = 1000
SEQ_LEN = 5
MAX_INT = 8


class LSTMTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Set seed
        torch.manual_seed(1)
        np.random.seed(1)

    def _split_dataset(self, X):
        return [X[:800], X[800:900], X[900:]]

    def test_lstm_memorize_first(self):
        X = torch.randint(1, MAX_INT + 1, (N,SEQ_LEN)).long()
        Y = X[:,0]

        Xs = self._split_dataset(X)
        Ys = self._split_dataset(Y)

        embed_size = 4
        hidden_size = 10
        vocab_size = MAX_INT + 1

        lstm_module = LSTMModule(embed_size, hidden_size, vocab_size, 
            bidirectional=False, verbose=False)
        em = EndModel(
            cardinality=MAX_INT, 
            input_module=lstm_module, 
            layer_output_dims=[hidden_size, MAX_INT],
            batchnorm=True,
            seed=1,
            verbose=False)
        em.train(Xs[0], Ys[0], Xs[1], Ys[1], n_epochs=10, verbose=False)
        score = em.score(Xs[2], Ys[2], verbose=False)
        self.assertGreater(score, 0.95)

    def test_lstm_memorize_marker(self):
        X = torch.randint(1, MAX_INT + 1, (N,SEQ_LEN)).long()
        Y = torch.zeros(N).long()
        needles = np.random.randint(1, SEQ_LEN - 1, N)
        for i in range(N):
            X[i, needles[i]] = MAX_INT + 1
            Y[i] = X[i, needles[i] + 1]

        Xs = self._split_dataset(X)
        Ys = self._split_dataset(Y)

        embed_size = 4
        hidden_size = 10
        vocab_size = MAX_INT + 2

        lstm_module = LSTMModule(embed_size, hidden_size, vocab_size, 
            bidirectional=True, verbose=False)
        em = EndModel(
            cardinality=MAX_INT, 
            input_module=lstm_module, 
            layer_output_dims=[hidden_size * 2, MAX_INT],
            batchnorm=True,
            seed=1,
            verbose=False)
        em.train(Xs[0], Ys[0], Xs[1], Ys[1], n_epochs=10, verbose=False)
        score = em.score(Xs[2], Ys[2], verbose=False)
        self.assertGreater(score, 0.95)


if __name__ == '__main__':
    unittest.main()        