/**
 * Server-Sent Events pass-through (ADR-0013). The generic /api rewrite (next.config.ts) buffers responses, which
 * would hold live updates back; this route streams the API's event stream to the browser chunk by chunk.
 */
const apiOrigin = process.env.API_ORIGIN ?? 'http://127.0.0.1:8000';

export const dynamic = 'force-dynamic';

export async function GET(req: Request): Promise<Response> {
  const upstream = await fetch(`${apiOrigin}/api/events/stream`, {
    headers: { cookie: req.headers.get('cookie') ?? '', accept: 'text/event-stream' },
    signal: req.signal, // the browser leaving closes the upstream stream too
    cache: 'no-store',
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      'Content-Type': upstream.headers.get('content-type') ?? 'text/event-stream',
      'Cache-Control': 'no-store, no-transform',
      'X-Accel-Buffering': 'no',
    },
  });
}
