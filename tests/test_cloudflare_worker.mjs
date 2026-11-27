import assert from "node:assert/strict";
import test from "node:test";
import handler from "../deploy/cloudflare-worker/worker.mjs";

test("routes a GET through the bound private service and preserves cookies", async () => {
  let forwarded;
  const env = {
    LILIN_VPC: {
      async fetch(request) {
        forwarded = request;
        return new Response("hi", {
          headers: { "set-cookie": "vh_session=test; Secure; HttpOnly", "server": "uvicorn" },
        });
      },
    },
  };
  const result = await handler.fetch(
    new Request("https://lilin-voice-hub.jp777.workers.dev/api/queue?guild_id=10", {
      headers: { cookie: "vh_session=test", "x-forwarded-for": "fake" },
    }),
    env,
  );
  assert.equal(forwarded.url, "http://lilin.internal/api/queue?guild_id=10");
  assert.equal(forwarded.headers.get("cookie"), "vh_session=test");
  assert.equal(forwarded.headers.get("x-forwarded-for"), null);
  assert.equal(forwarded.headers.get("x-forwarded-proto"), "https");
  assert.equal(result.headers.get("set-cookie"), "vh_session=test; Secure; HttpOnly");
  assert.equal(result.headers.get("cache-control"), "no-store");
  assert.equal(result.headers.get("server"), null);
});

test("forwards request bodies and origin checks unchanged", async () => {
  let posted;
  const env = {
    LILIN_VPC: {
      async fetch(request) {
        posted = { method: request.method, origin: request.headers.get("origin"), body: await request.text() };
        return Response.json({ ok: true });
      },
    },
  };
  const origin = "https://lilin-voice-hub.jp777.workers.dev";
  const result = await handler.fetch(
    new Request(`${origin}/api/settings`, { method: "POST", headers: { origin }, body: '{"voice":"th-TH"}' }),
    env,
  );
  assert.deepEqual(posted, { method: "POST", origin, body: '{"voice":"th-TH"}' });
  assert.deepEqual(await result.json(), { ok: true });
});

test("returns a Thai error without exposing internal service addresses", async () => {
  const env = { LILIN_VPC: { async fetch() { throw new Error("private failure secret"); } } };
  const original = console.error;
  console.error = () => {};
  try {
    const result = await handler.fetch(new Request("https://lilin-voice-hub.jp777.workers.dev/"), env);
    assert.equal(result.status, 502);
    const body = await result.text();
    assert.match(body, /ลิลิน/);
    assert.doesNotMatch(body, /secret|lilin\.internal/);
  } finally {
    console.error = original;
  }
});
