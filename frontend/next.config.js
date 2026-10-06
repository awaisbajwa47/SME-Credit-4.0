/** Proxy /api/* to the FastAPI backend so the browser never deals with CORS. */
module.exports = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
  },
};
