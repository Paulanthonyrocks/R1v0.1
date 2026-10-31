export const dynamic = 'force-dynamic';

// TEMPORARY diagnostic probe — deleted after the env-semantics test.
export async function GET() {
    const env = process.env as Record<string, string | undefined>;
    return Response.json({
        dotPublic: process.env.NEXT_PUBLIC_API_BASE_URL ?? null,
        bracketPublic: process.env['NEXT_PUBLIC_API_BASE_URL'] ?? null,
        computedPublic: process.env['NEXT_PUBLIC_' + 'API_BASE_URL'] ?? null,
        dotTest: process.env.TEST_PUBLIC ?? null,
        bracketTest: process.env['TEST_PUBLIC'] ?? null,
        serverOnly: process.env.TEST_SERVER ?? null,
    });
}
