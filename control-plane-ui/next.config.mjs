/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Allow alternate output dirs when .next is not writable (e.g. root-owned CI artifacts).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  async redirects() {
    return [
      // /finops/usage is now a real route. Only the old single-page model anchor needs a redirect.
      {
        source: "/finops/models",
        destination: "/finops/usage?dimension=model",
        permanent: false,
      },
      { source: "/login", destination: "/", permanent: false },
      { source: "/administration/users", destination: "/organization/team", permanent: false },
    ];
  },
};

export default nextConfig;
