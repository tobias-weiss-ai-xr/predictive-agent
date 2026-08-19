"""State persistence to PVC (JSON files)."""

import json
import os

from predictive_agent.config import PREDICTIONS_FILE
from predictive_agent.markov import MarkovChain


class StateStore:
    """Save/load Markov chain state and predictions to JSON files.

    Creates parent directories on save and returns sensible defaults when
    loading from nonexistent or corrupted files.
    """

    def __init__(self, state_model_file, predictions_file=PREDICTIONS_FILE):
        self.state_model_file = state_model_file
        self.predictions_file = predictions_file

    def save_markov(self, chain):
        """Persist a MarkovChain to the state-model JSON file (atomic write)."""
        self._ensure_dir(self.state_model_file)
        self._atomic_write(self.state_model_file, chain.to_dict())

    def load_markov(self):
        """Load a MarkovChain from file; return a fresh chain if missing/corrupt."""
        if not os.path.exists(self.state_model_file):
            return MarkovChain()
        try:
            with open(self.state_model_file, "r", encoding="utf-8") as f:
                return MarkovChain.from_dict(json.load(f))
        except (json.JSONDecodeError, OSError, ValueError):
            # Corrupted state file (e.g. truncated write on PVC eviction);
            # fall back to a fresh chain rather than crashing the operator.
            return MarkovChain()

    def save_predictions(self, predictions):
        """Persist predictions (list of dicts) to the predictions JSON file (atomic write)."""
        self._ensure_dir(self.predictions_file)
        self._atomic_write(self.predictions_file, predictions)

    def load_predictions(self):
        """Load predictions from file; return an empty list if missing/corrupt."""
        if not os.path.exists(self.predictions_file):
            return []
        try:
            with open(self.predictions_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, ValueError):
            # Corrupted predictions file; fall back to empty list.
            return []

    @staticmethod
    def _ensure_dir(path):
        """Create the parent directory of ``path`` if it does not exist."""
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

    @staticmethod
    def _atomic_write(path, data):
        """Write JSON atomically: write to a temp file then os.replace.

        This prevents readers from observing a half-written (corrupted) file
        if the process is killed mid-write, e.g. during pod eviction.
        """
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
