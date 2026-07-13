"""Shared state for the interactive REPL and dashboard."""

import threading
import networkx as nx

class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.G = nx.DiGraph()
        self.ranked = []
        self.config = None
        self.engine_url = "http://localhost:8081"
        # We don't initialize the client immediately to avoid missing arguments
        self.client = None
        self.incidents = {}

SHARED_STATE = SharedState()
