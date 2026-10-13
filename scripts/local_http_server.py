"""Local probe servers do not need reverse DNS to bind a listening socket."""
from http.server import HTTPServer
from socketserver import TCPServer, ThreadingMixIn


class LocalHTTPServer(HTTPServer):
    def server_bind(self):
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class LocalThreadingHTTPServer(ThreadingMixIn, LocalHTTPServer):
    daemon_threads = True
