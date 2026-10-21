import * as fs from 'fs';
import * as path from 'path';
import { sanitizeTunnelUrl } from '../../lib/api/backendBaseUrl';

describe('sanitizeTunnelUrl', () => {
  it('redacts the tunnel password in absolute URLs', () => {
    expect(
      sanitizeTunnelUrl('wss://example.com/api/v1/ws/abc?password=35.252.254.9')
    ).toBe('wss://example.com/api/v1/ws/abc?password=REDACTED');
  });

  it('redacts among other params and keeps them intact', () => {
    const out = sanitizeTunnelUrl(
      'https://example.com/api/v1/analytics/history/f1?hours=24&password=secret&x=1'
    );
    expect(out).toContain('password=REDACTED');
    expect(out).toContain('hours=24');
    expect(out).toContain('x=1');
    expect(out).not.toContain('password=secret');
  });

  it('passes through URLs without a password untouched', () => {
    const url = 'https://example.com/api/v1/logs?limit=10';
    expect(sanitizeTunnelUrl(url)).toBe(url);
  });

  it('never leaks the secret even on unparseable input', () => {
    const out = sanitizeTunnelUrl(':::not a url:::?password=supersecret');
    expect(out).not.toContain('supersecret');
  });

  it('redacts tunnel_token when it appears in a URL anyway', () => {
    const out = sanitizeTunnelUrl(
      'wss://example.com/api/v1/ws/abc?tunnel_token=abc123'
    );
    expect(out).toContain('tunnel_token=REDACTED');
    expect(out).not.toContain('abc123');
  });
});

describe('tunnelAuthProtocols', () => {
  const original = process.env.NEXT_PUBLIC_TUNNEL_AUTH_TOKEN;

  afterEach(() => {
    if (original === undefined) delete process.env.NEXT_PUBLIC_TUNNEL_AUTH_TOKEN;
    else process.env.NEXT_PUBLIC_TUNNEL_AUTH_TOKEN = original;
    // The module caches the token after the first read; reset so each case
    // observes the env var it just set (not an earlier case's cached value).
    jest.resetModules();
  });

  it('offers the token as a subprotocol name when configured', async () => {
    process.env.NEXT_PUBLIC_TUNNEL_AUTH_TOKEN = 'TOK123';
    const { tunnelAuthProtocols, TUNNEL_AUTH_SUBPROTOCOL_PREFIX } =
      await import('../../lib/api/backendBaseUrl');
    expect(tunnelAuthProtocols()).toEqual([
      TUNNEL_AUTH_SUBPROTOCOL_PREFIX + 'TOK123',
    ]);
  });

  it('offers nothing when unconfigured', async () => {
    delete process.env.NEXT_PUBLIC_TUNNEL_AUTH_TOKEN;
    const { tunnelAuthProtocols } =
      await import('../../lib/api/backendBaseUrl');
    expect(tunnelAuthProtocols()).toEqual([]);
  });

  it('never puts the token in a URL query string', async () => {
    process.env.NEXT_PUBLIC_TUNNEL_AUTH_TOKEN = 'TOK123';
    const { tunnelAuthProtocols } =
      await import('../../lib/api/backendBaseUrl');
    const offered = tunnelAuthProtocols();
    // The whole point of the subprotocol form: Chrome's native WS-failure
    // message prints the URL, so the secret must not ride in one.
    for (const protocol of offered) {
      expect(protocol).not.toMatch(/tunnel_token|password/);
    }
  });

  it('stays byte-identical to the backend SUBPROTOCOL_PREFIX', async () => {
    // Cross-language contract: the backend strip-prefix lookup in
    // app/utils/tunnel_auth.py must match this constant or every WS upgrade
    // 403s once the query form is dropped.
    const backendSource = fs.readFileSync(
      path.resolve(
        __dirname,
        '../../../backend/app/utils/tunnel_auth.py'
      ),
      'utf8'
    );
    const match = backendSource.match(/^SUBPROTOCOL_PREFIX = "([^"]+)"/m);
    expect(match).not.toBeNull();
    const { TUNNEL_AUTH_SUBPROTOCOL_PREFIX } =
      await import('../../lib/api/backendBaseUrl');
    expect(TUNNEL_AUTH_SUBPROTOCOL_PREFIX).toBe(match![1]);
  });
});
