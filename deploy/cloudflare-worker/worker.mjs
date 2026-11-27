/**
 * HTTPS entry point for Lilin's Thai dashboard, with no custom domain.
 * LILIN_VPC is a Cloudflare Workers VPC Service bound to Wispbyte localhost.
 * The origin connection is encrypted through Cloudflare Tunnel, not public HTTP.
 */

export default {
  async fetch(request, env) {
    const incoming = new URL(request.url);
    const upstream = new URL("http://lilin.internal/");
    upstream.pathname = incoming.pathname;
    upstream.search = incoming.search;

    const headers = new Headers(request.headers);
    for (const name of ["cf-connecting-ip", "x-forwarded-for", "x-forwarded-host", "x-real-ip"]) {
      headers.delete(name);
    }
    headers.set("x-forwarded-proto", "https");
    headers.set("x-forwarded-host", incoming.host);

    try {
      const forwarded = new Request(upstream, {
        method: request.method,
        headers,
        body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
        redirect: "manual",
        duplex: "half",
      });
      const response = await env.LILIN_VPC.fetch(forwarded);
      const responseHeaders = new Headers(response.headers);
      responseHeaders.delete("server");
      responseHeaders.set("strict-transport-security", "max-age=31536000");
      responseHeaders.set("x-content-type-options", "nosniff");
      responseHeaders.set("referrer-policy", "no-referrer");
      responseHeaders.set("x-frame-options", "DENY");
      if (incoming.pathname.startsWith("/api/") || incoming.pathname.startsWith("/auth/")) {
        responseHeaders.set("cache-control", "no-store");
      }
      const location = responseHeaders.get("location");
      if (location?.startsWith("http://lilin.internal/")) {
        const redirect = new URL(location);
        responseHeaders.set("location", `${redirect.pathname}${redirect.search}${redirect.hash}`);
      }
      return new Response(response.body, { status: response.status, headers: responseHeaders });
    } catch (error) {
      console.error(JSON.stringify({ event: "lilin_origin_unavailable", type: error?.name || "Error" }));
      return new Response("ขออภัยค่ะ ลิลินยังเชื่อมต่อกับเซิร์ฟเวอร์ไม่ได้ กรุณาลองใหม่อีกครั้ง", {
        status: 502,
        headers: {
          "content-type": "text/plain; charset=utf-8",
          "cache-control": "no-store",
          "x-content-type-options": "nosniff",
        },
      });
    }
  },
};
