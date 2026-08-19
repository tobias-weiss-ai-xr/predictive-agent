"""State persistence to PVC (JSON files)."""

import json
import os

from dev_agent.config import PREDICTIONS_FILE
from dev_agent.markov import MarkovChain


class StateStore:
    """Save/load Markov chain state and predictions to JSON files.

    Creates parent directories on save and returns sensible defaults when
    loading from nonexistent files.
    """

    def __init__(self, state_model_file, predictions_file=PREDICTIONS_FILE):
        self.state_model_file = state_model_file
        self.predictions_file = predictions_file

    def save_markov(self, chain):
        """Persist a MarkovChain to the state-model JSON file."""
        self._ensure_dir(self.state_model_file)
        with open(self.state_model_file, "w", encoding="utf-8") as f:
            json.dump(chain.to_dict(), f)

    def load_markov(self):
        """Load a MarkovChain from file; return a fresh chain if missing."""
        if not os.path.exists(self.state_model_file):
            return MarkovChain()
        with open(self.state_model_file, "r", encoding="utf-8") as f:
            return MarkovChain.from_dict(json.load(f))

    def save_predictions(self, predictions):
        """Persist predictions (list of dicts) to the predictions JSON file."""
        self._ensure_dir(self.predictions_file)
        with open(self.predictions_file, "w", encoding="utf-8") as f:
            json.dump(predictions, f)

    def load_predictions(self):
        """Load predictions from file; return an empty list if missing."""
        if not os.path.exists(self.predictions_file):
            return []
        with open(self.predictions_file, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _ensure_dir(path):
        """Create the parent directory of ``path`` if it does not exist."""
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
