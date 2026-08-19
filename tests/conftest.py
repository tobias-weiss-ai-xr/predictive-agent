"""Pytest configuration for dev-agent tests."""
import sys
import os

# Add project root to path so we can import dev_agent package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
