import logging
from urllib.parse import urlparse

from odoo import http
from odoo.fields import Date
from odoo.http import request

_logger = logging.getLogger(__name__)

# Only allow redirects to localhost (security)
ALLOWED_CALLBACK_HOSTS = {"127.0.0.1", "localhost"}


class McpApiKeyController(http.Controller):

    @http.route("/mcp/setup", type="http", auth="user", website=False)
    def setup_page(self, callback_port="18069", **kwargs):
        """Render the API Key generation page.

        The user must be logged in (SAML/password session).
        """
        return request.render(
            "mcp_api_key.setup_page",
            {
                "callback_port": int(callback_port),
                "user_name": request.env.user.name,
                "user_email": request.env.user.email or request.env.user.login,
            },
        )

    @http.route(
        "/mcp/setup/generate",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=True,
    )
    def generate_key(self, callback_port="18069", **kwargs):
        """Generate an API Key and redirect to the local wizard callback."""
        # Validate callback target is localhost
        port = int(callback_port)
        callback_url = f"http://127.0.0.1:{port}/callback"
        parsed = urlparse(callback_url)
        if parsed.hostname not in ALLOWED_CALLBACK_HOSTS:
            return request.not_found()

        user = request.env.user
        # Odoo 18: _generate() requires expiration_date (None = no expiry)
        api_key = request.env["res.users.apikeys"]._generate(
            "rpc", f"Claude MCP Server ({user.name})", None
        )

        _logger.info(
            "MCP API Key generated for user %s (id=%s)",
            user.login,
            user.id,
        )

        # Redirect to local wizard with the key in URL fragment (#)
        # Fragment is NOT sent to server logs or proxy — safer than query string
        login = user.email or user.login
        redirect_url = f"{callback_url}#api_key={api_key}&login={login}"
        return request.redirect(redirect_url, code=302, local=False)
