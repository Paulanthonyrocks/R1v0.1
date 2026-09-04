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
});
