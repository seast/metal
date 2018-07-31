import sys
import unittest

import numpy as np

sys.path.append("../metal")
from metal.tuner import ModelTuner

class TunerTest(unittest.TestCase):

    def test_config_constant(self):
        search_space = {'a': 1}
        tuner = ModelTuner(None, None, 123)
        configs = list(tuner.config_generator(search_space, max_search=10))
        self.assertEqual(len(configs), 1)

    def test_config_list(self):
        search_space = {'a': [1, 2]}
        tuner = ModelTuner(None, None, 123)
        configs = list(tuner.config_generator(search_space, max_search=10))
        self.assertEqual(len(configs), 2)

    def test_config_two_values(self):
        search_space = {'a': [1],
                        'b': [1, 2, 3]}
        tuner = ModelTuner(None, None, 123)
        configs = list(tuner.config_generator(search_space, max_search=10))
        self.assertEqual(len(configs), 3)

    def test_config_range(self):
        search_space = {'a': [1],
                        'b': [1, 2, 3],
                        'c': {'range': [1, 10]}}
        tuner = ModelTuner(None, None, 123)                        
        configs = list(tuner.config_generator(search_space, max_search=10))
        self.assertEqual(len(configs), 10)

    def test_config_unbounded_max_search(self):
        search_space = {'a': [1],
                        'b': [1, 2, 3],
                        'c': {'range': [1, 10]}}
        tuner = ModelTuner(None, None, 123)                        
        configs = list(tuner.config_generator(search_space, max_search=0))
        self.assertEqual(len(configs), 3)

    def test_config_log_range(self):
        search_space = {'a': [1],
                        'b': [1, 2, 3],
                        'c': {'range': [1, 10]},
                        'd': {'range': [1, 10], 'scale': 'log'}}
        tuner = ModelTuner(None, None, 123)                        
        configs = list(tuner.config_generator(search_space, max_search=20))
        self.assertEqual(len(configs), 20)
        self.assertGreater(
            np.mean([c['c'] for c in configs]), 
            np.mean([c['d'] for c in configs]))


if __name__ == '__main__':
    unittest.main()