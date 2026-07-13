"""Local HTTP server and request handler for the React dashboard UI."""

import os
import json
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer
from pathlib import Path
from chaosrank.cli_utils import console

class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, shared_state=None, repl_instance=None, **kwargs):
        self.shared_state = shared_state
        self.repl_instance = repl_instance
        super().__init__(*args, **kwargs)
        
    ALLOWED_ORIGIN = 'http://localhost:8082'

    def _cors_origin(self) -> str:
        origin = self.headers.get('Origin', '')
        if origin.startswith('http://localhost') or origin.startswith('http://127.0.0.1'):
            return origin
        return self.ALLOWED_ORIGIN

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', self._cors_origin())
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        if self.path == '/api/command':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                cmd_str = data.get('cmd', '')
                
                # Execute command via REPL
                if self.repl_instance and cmd_str:
                    # Run in a separate thread so we don't block the HTTP response
                    threading.Thread(target=self.repl_instance.process_command, args=(cmd_str,)).start()
                elif self.shared_state and self.shared_state.client and (cmd_str.startswith("set ") or cmd_str == "reset config"):
                    def _update_and_rerank():
                        try:
                            with self.shared_state.lock:
                                cfg = self.shared_state.config
                                if cmd_str == "reset config":
                                    cfg["weights"]["blast_radius"] = 0.6
                                    cfg["weights"]["fragility"] = 0.4
                                    cfg["fragility"]["decay_lambda"] = 0.1
                                    cfg["graph"]["use_betweenness"] = False
                                    cfg["graph"]["w_bc"] = 0.2
                                    cfg["graph"]["w_pr"] = 0.5
                                    cfg["graph"]["w_od"] = 0.5
                                else:
                                    parts = cmd_str.split(" ")
                                    if len(parts) >= 3:
                                        key = parts[1]
                                        val = parts[2]
                                        if key == "alpha":
                                            cfg["weights"]["blast_radius"] = float(val)
                                            cfg["weights"]["fragility"] = 1.0 - float(val)
                                        elif key == "decay_lambda":
                                            cfg["fragility"]["decay_lambda"] = float(val)
                                        elif key == "w_pr":
                                            cfg["graph"]["w_pr"] = float(val)
                                        elif key == "w_od":
                                            cfg["graph"]["w_od"] = float(val)
                                        elif key == "w_bc":
                                            cfg["graph"]["w_bc"] = float(val)
                                        elif key == "use_betweenness":
                                            cfg["graph"]["use_betweenness"] = val.lower() == "true"
                                
                                self.shared_state.ranked = self.shared_state.client.rank(
                                    self.shared_state.G, 
                                    self.shared_state.incidents, 
                                    config=cfg
                                )
                        except Exception as e:
                            print(f"Failed to update config via UI: {e}")
                    threading.Thread(target=_update_and_rerank).start()
                    
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', self._cors_origin())
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode())
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', self._cors_origin())
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
            return
            
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/state':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', self._cors_origin())
            self.end_headers()
            
            edges = []
            nodes = []
            ranked = []
            config_dict = {}
            
            if self.shared_state:
                with self.shared_state.lock:
                    if self.shared_state.G:
                        for u, v, data in self.shared_state.G.edges(data=True):
                            edges.append({
                                "source": u,
                                "target": v,
                                "weight": data.get("weight", 1.0),
                                "edge_type": data.get("edge_type", "sync")
                            })
                        nodes = list(self.shared_state.G.nodes())
                    ranked = self.shared_state.ranked
                    
                    config = self.shared_state.config
                    if isinstance(config, dict):
                        graph_cfg = config.get("graph", {})
                        config_dict = {
                            "alpha": config.get("weights", {}).get("blast_radius", 0.6),
                            "beta": config.get("weights", {}).get("fragility", 0.4),
                            "lambda": config.get("fragility", {}).get("decay_lambda", 0.1),
                            "use_betweenness": graph_cfg.get("use_betweenness", False),
                            "w_bc": graph_cfg.get("w_bc", 0.2),
                            "w_pr": graph_cfg.get("w_pr", 0.5),
                            "w_od": graph_cfg.get("w_od", 0.5)
                        }
                    else:
                        config_dict = {}
            
            payload = {
                "nodes": nodes,
                "edges": edges,
                "ranking": ranked,
                "config": config_dict
            }
            self.wfile.write(json.dumps(payload).encode())
            return
            
        # Fallback for SPA routing
        path = self.translate_path(self.path)
        if not os.path.exists(path) and '.' not in os.path.basename(self.path):
            self.path = '/'
            
        return super().do_GET()

def start_ui_server(shared_state=None, repl_instance=None, port=8082):
    # Locate the bundled UI dist folder
    base_dir = Path(__file__).resolve().parent.parent / "ui_dist"
    
    if not base_dir.exists():
        console.print(f"[red]Error: UI bundle not found at {base_dir}[/red]")
        return
        
    def handler(*args, **kwargs):
        return DashboardHandler(
            *args, 
            directory=str(base_dir), 
            shared_state=shared_state,
            repl_instance=repl_instance,
            **kwargs
        )
    
    class ReusableTCPServer(TCPServer):
        allow_reuse_address = True
        
    try:
        httpd = ReusableTCPServer(("", port), handler)
        url = f"http://localhost:{port}"
        
        console.print(f"\n[bold green]🚀 ChaosRank Dashboard Server running at {url}[/bold green]")
        
        # Open browser automatically
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        
        httpd.serve_forever()
    except OSError as e:
        if e.errno in (98, 10048):
            console.print(f"[red]Port {port} is already in use.[/red]")
        else:
            pass
