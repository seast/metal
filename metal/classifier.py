from metal.metrics import metric_score, confusion_matrix
import torch.nn as nn

class Classifier(nn.Module):
    """Simple abstract base class for a probabilistic classifier."""

    def __init__(self, cardinality=2, name=None):
        self.name = name or self.__class__.__name__
        self.cardinality = cardinality

    def train(self, X, **kwargs):
        raise NotImplementedError

    def predict(self, X, **kwargs):
        raise NotImplementedError

    def score(self, X, Y, metric='accuracy', verbose=True, **kwargs):
        Y_p = self.predict(X, **kwargs)
        score = metric_score(Y, Y_p, metric, ignore_in_gold=[0], **kwargs)
        if verbose:
            print(f"{metric.capitalize()}: {score:.3f}")
        return score
 
    def confusion(self, X, Y, **kwargs):
        # TODO: implement this here
        raise NotImplementedError
    
    def error_analysis(self, session, X, Y):
        # TODO: implement this here
        raise NotImplementedError

    def save(self):
        raise NotImplementedError

    def load(self):
        raise NotImplementedError
    
    def _check_or_set_attr(self, name, val, set_val=False):
        if set_val:
            setattr(self, name, val)
        else:
            true_val = getattr(self, name)
            if val != true_val:
                raise Exception(f"{name} = {val}, but should be {true_val}.")