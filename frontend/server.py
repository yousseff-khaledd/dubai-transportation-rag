"""Serve the local chat page and forward its API requests. No extra dependencies."""
import json
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FRONTEND_DIRECTORY = Path(__file__).resolve().parent
STATIC_FILES = {
    '/': ('index.html', 'text/html'),
    '/styles.css': ('styles.css', 'text/css'),
    '/app.js': ('app.js', 'text/javascript'),
}


class FrontendHandler(BaseHTTPRequestHandler):
    def send_content(self, status, content, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The user closed or refreshed the page.

    def proxy_request(self, method, body=None):
        connection = HTTPConnection('127.0.0.1', 8000, timeout=5 if method == 'GET' else 900)
        try:
            connection.request(method, self.path.removeprefix('/api'), body,
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            self.send_content(response.status, response.read(), 'application/json')
        except (OSError, HTTPException):
            content = json.dumps({'error': {'code': 'api_unreachable', 'message':
                'Cannot reach the API at 127.0.0.1:8000. Check that your backend is running.'}}).encode()
            self.send_content(502, content, 'application/json')
        finally:
            connection.close()

    def do_GET(self):
        if self.path == '/api/health':
            self.proxy_request('GET')
        elif self.path in STATIC_FILES:
            filename, content_type = STATIC_FILES[self.path]
            self.send_content(200, (FRONTEND_DIRECTORY / filename).read_bytes(), content_type)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path not in ('/api/chat', '/api/chat/resume'):
            self.send_error(404)
            return
        try:
            content_length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.send_error(400)
            return
        if not 0 < content_length <= 32768:
            self.send_error(413)
            return
        self.proxy_request('POST', self.rfile.read(content_length))


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 3000), FrontendHandler)
    print('Chat page: http://127.0.0.1:3000', flush=True)
    print('Using the existing API at http://127.0.0.1:8000', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
