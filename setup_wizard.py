"""
Setup Wizard - Browser-based MCP Server configuration

Flow:
1. User selects environment (if deploy-config.json exists)
2. Browser opens Odoo's /mcp/setup page (user is logged in via SAML)
3. User clicks "Connect Claude Code" on Odoo
4. Odoo generates API Key and redirects to localhost callback
5. Wizard captures the key, writes .env, registers MCP server
6. Done - user never sees or touches the API Key
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

INSTALL_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = INSTALL_DIR / "deploy-config.json"
ENV_PATH = INSTALL_DIR / ".env"
WIZARD_PORT = 18069

DEFAULT_CONFIG = {
    "environments": {},
    "defaults": {"readonly_mode": True, "view_filtered_mode": True},
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return DEFAULT_CONFIG


def build_env_select_html(config: dict) -> str:
    """Page 1: Select environment, then redirect to Odoo."""
    envs = config.get("environments", {})
    has_envs = bool(envs)

    if has_envs:
        env_options = ""
        for key, env in envs.items():
            checked = "checked" if key == "sit" else ""
            if not any(k == "sit" for k in envs):
                checked = "checked" if not env_options else ""
            env_options += f"""
            <label class="env-option">
                <input type="radio" name="env" value="{key}" {checked}
                       data-url="{env['odoo_url']}" data-db="{env['database']}">
                <span class="env-card">
                    <strong>{env['name']}</strong>
                    <small>{env['odoo_url']}</small>
                </span>
            </label>"""
        env_section = f'<div class="env-group">{env_options}</div>'
    else:
        env_section = """
        <label>Odoo URL
            <input type="url" id="manual-url" placeholder="https://your-odoo.com/" required>
        </label>
        <label>Database Name
            <input type="text" id="manual-db" placeholder="your_database" required>
        </label>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Odoo MCP Server Setup</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f5f5f7; color: #1d1d1f;
    min-height: 100vh; display: flex; justify-content: center; padding: 40px 20px;
  }}
  .container {{ max-width: 480px; width: 100%; }}
  h1 {{ font-size: 28px; margin-bottom: 8px; }}
  .subtitle {{ color: #86868b; margin-bottom: 32px; font-size: 15px; }}
  .section {{
    background: #fff; border-radius: 12px; padding: 24px;
    margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }}
  .section h2 {{ font-size: 16px; margin-bottom: 16px; }}
  label {{ display: block; font-size: 14px; color: #6e6e73; margin-bottom: 12px; }}
  input[type="text"], input[type="url"] {{
    width: 100%; padding: 10px 14px; border: 1px solid #d2d2d7;
    border-radius: 8px; font-size: 15px; margin-top: 4px;
  }}
  input:focus {{ outline: none; border-color: #0071e3; }}
  .env-group {{ display: flex; flex-direction: column; gap: 8px; }}
  .env-option {{ cursor: pointer; }}
  .env-option input {{ display: none; }}
  .env-card {{
    display: block; padding: 14px 16px; border: 2px solid #d2d2d7;
    border-radius: 10px; transition: all 0.2s;
  }}
  .env-card small {{ display: block; color: #86868b; margin-top: 2px; font-size: 12px; }}
  .env-option input:checked + .env-card {{ border-color: #0071e3; background: #f0f7ff; }}
  .btn {{
    width: 100%; padding: 14px; background: #0071e3; color: #fff;
    border: none; border-radius: 10px; font-size: 16px; font-weight: 600;
    cursor: pointer; transition: background 0.2s;
  }}
  .btn:hover {{ background: #0077ED; }}
  .steps {{
    background: #f5f5f7; border-radius: 8px; padding: 16px;
    font-size: 13px; color: #6e6e73; line-height: 1.8; margin-bottom: 16px;
  }}
  .steps li {{ margin-left: 16px; }}
</style>
</head>
<body>
<div class="container">
  <h1>Odoo MCP Setup</h1>
  <p class="subtitle">Connect Claude Code to your Odoo instance</p>

  <div class="section">
    <h2>Select Environment</h2>
    {env_section}
  </div>

  <div class="section">
    <h2>What happens next</h2>
    <ol class="steps">
      <li>Your browser opens the Odoo login page</li>
      <li>Sign in with your Google account (if not already)</li>
      <li>Click <strong>"Connect Claude Code"</strong></li>
      <li>Done! Everything is set up automatically</li>
    </ol>
    <button class="btn" onclick="startSetup()">Open Odoo &rarr;</button>
  </div>
</div>

<script>
function startSetup() {{
  let odooUrl, db;
  const radio = document.querySelector('input[name="env"]:checked');
  if (radio) {{
    odooUrl = radio.dataset.url;
    db = radio.dataset.db;
  }} else {{
    odooUrl = document.getElementById('manual-url')?.value;
    db = document.getElementById('manual-db')?.value;
  }}
  if (!odooUrl) {{ alert('Please select an environment.'); return; }}

  // Store selected env info for the callback
  fetch('/select-env', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ odoo_url: odooUrl, database: db }})
  }}).then(() => {{
    // Open Odoo's MCP setup page
    const setupUrl = odooUrl.replace(/\\/$/, '') + '/mcp/setup?callback_port={WIZARD_PORT}';
    window.location.href = setupUrl;
  }});
}}
</script>
</body>
</html>"""


# Landing page that reads API Key from URL fragment and POSTs to server
# Fragment (#) never reaches server logs — more secure than query string
CALLBACK_LANDING_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Connecting...</title>
<style>
  body { font-family: -apple-system, sans-serif; display: flex;
         align-items: center; justify-content: center; min-height: 100vh;
         background: #f5f5f7; margin: 0; }
  .msg { text-align: center; color: #86868b; }
</style></head><body>
<div class="msg"><p>Completing setup...</p></div>
<script>
(function() {
    var hash = window.location.hash.substring(1);
    var params = {};
    hash.split('&').forEach(function(part) {
        var kv = part.split('=');
        if (kv.length === 2) params[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1]);
    });
    // Clear fragment from URL immediately (don't leave API key in address bar)
    history.replaceState(null, '', window.location.pathname);

    if (!params.api_key) {
        window.location.href = '/callback-error';
        return;
    }
    fetch('/callback', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(params)
    }).then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.success) {
            document.body.innerHTML = '<div class="msg"><p>Redirecting...</p></div>';
            window.location.href = '/success?login=' + encodeURIComponent(data.login);
        } else {
            document.body.innerHTML = '<div class="msg"><h2>Error</h2><p>' + data.error + '</p></div>';
        }
    }).catch(function(e) {
        document.body.innerHTML = '<div class="msg"><h2>Error</h2><p>' + e.message + '</p></div>';
    });
})();
</script></body></html>"""


def build_success_html(login: str) -> str:
    """Success page shown after callback."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Setup Complete</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f5f5f7;
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
  }}
  .card {{
    background: #fff; border-radius: 16px; padding: 48px 40px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    max-width: 420px; text-align: center;
  }}
  .check {{
    width: 64px; height: 64px; background: #34c759;
    border-radius: 50%; margin: 0 auto 24px;
    display: flex; align-items: center; justify-content: center;
    font-size: 32px; color: #fff;
  }}
  h1 {{ font-size: 22px; margin-bottom: 8px; }}
  .info {{ color: #86868b; font-size: 14px; margin-bottom: 24px; }}
  .info strong {{ color: #1d1d1f; }}
  .try-it {{
    background: #f0f7ff; border-radius: 10px; padding: 16px;
    font-size: 14px; color: #0071e3; line-height: 1.6;
  }}
  .close-note {{ margin-top: 20px; font-size: 12px; color: #86868b; }}
</style>
</head>
<body>
<div class="card">
  <div class="check">&#10003;</div>
  <h1>Setup Complete!</h1>
  <p class="info">Connected as <strong>{login}</strong></p>
  <div class="try-it">
    Open Claude Code and try:<br>
    <strong>"How many employees do we have?"</strong><br>
    <strong>"Show me today's leave requests"</strong>
  </div>
  <p class="close-note">You can close this page now.</p>
</div>
</body>
</html>"""


class WizardHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for the setup wizard."""

    config: dict = {}
    selected_env: dict = {}  # {odoo_url, database}
    result: dict | None = None

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/":
            # Page 1: Environment selection
            html = build_env_select_html(self.config)
            self._html_response(html)

        elif parsed.path == "/callback":
            # Odoo redirects here with api_key in URL fragment (#)
            # Fragment is NOT sent to server — serve a JS page that reads it and POSTs
            self._html_response(CALLBACK_LANDING_HTML)

        elif parsed.path == "/callback-error":
            self._html_response("<h1>Error: No API Key received</h1>", 400)

        elif parsed.path == "/success":
            params = urllib.parse.parse_qs(parsed.query)
            login = params.get("login", [""])[0]
            self._html_response(build_success_html(login))

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/select-env":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            WizardHandler.selected_env = body
            self._json_response({"ok": True})

        elif self.path == "/callback":
            # JS page POSTs the fragment data here
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            api_key = body.get("api_key", "")
            login = body.get("login", "")

            if not api_key:
                self._json_response({"success": False, "error": "No API Key"})
                return

            env_info = WizardHandler.selected_env
            success, error = self._finalize_setup(env_info, login, api_key)

            if success:
                WizardHandler.result = {"login": login, "api_key": "***"}
                self._json_response({"success": True, "login": login})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._json_response({"success": False, "error": error})
        else:
            self.send_response(404)
            self.end_headers()

    def _finalize_setup(self, env_info: dict, login: str, api_key: str) -> tuple[bool, str]:
        """Write .env and register MCP server."""
        defaults = self.config.get("defaults", {})
        readonly = str(defaults.get("readonly_mode", True)).lower()
        view_filtered = str(defaults.get("view_filtered_mode", True)).lower()

        odoo_url = env_info.get("odoo_url", "")
        database = env_info.get("database", "")

        if not odoo_url or not database:
            return False, "Environment not selected. Please go back and select one."

        env_content = (
            f"ODOO_URL={odoo_url}\n"
            f"ODOO_DATABASE={database}\n"
            f"ODOO_LOGIN={login}\n"
            f"ODOO_API_KEY={api_key}\n"
            f"READONLY_MODE={readonly}\n"
            f"VIEW_FILTERED_MODE={view_filtered}\n"
        )
        ENV_PATH.write_text(env_content)

        # Register MCP server in Claude Code
        venv_python = INSTALL_DIR / ".venv" / "bin" / "python"
        python_cmd = str(venv_python) if venv_python.exists() else sys.executable
        server_script = str(INSTALL_DIR / "odoo_mcp_server.py")

        try:
            subprocess.run(
                ["claude", "mcp", "remove", "odoo-mcp-server"],
                capture_output=True, timeout=10,
            )
            subprocess.run(
                ["claude", "mcp", "add", "odoo-mcp-server", "--", python_cmd, server_script],
                capture_output=True, check=True, timeout=10,
            )
        except FileNotFoundError:
            return False, "Claude Code CLI not found."
        except subprocess.CalledProcessError as e:
            return False, f"Failed to register MCP: {e.stderr.decode()}"

        return True, ""

    def _html_response(self, html: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _json_response(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))


def run_wizard():
    config = load_config()
    WizardHandler.config = config

    server = http.server.HTTPServer(("127.0.0.1", WIZARD_PORT), WizardHandler)

    url = f"http://127.0.0.1:{WIZARD_PORT}"
    print(f"\n  Setup wizard: {url}\n")
    print("  Waiting for setup to complete...\n")

    webbrowser.open(url)
    server.serve_forever()

    if WizardHandler.result:
        print("  Setup completed successfully!")
        print("  You can now use Claude Code with Odoo.\n")
        return 0
    else:
        print("  Setup was cancelled.\n")
        return 1


if __name__ == "__main__":
    sys.exit(run_wizard())
