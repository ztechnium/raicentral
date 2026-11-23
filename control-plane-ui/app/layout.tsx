import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "RAI Control Plane — Govern AI Actions at Runtime",
    template: "%s | RAI Control Plane",
  },
  description:
    "Runtime governance for AI agents — register, authorize, enforce policies, and produce audit-ready evidence for every consequential action.",
  keywords: [
    "AI governance",
    "runtime enforcement",
    "responsible AI",
    "agent governance",
    "AI policy",
    "audit trail",
  ],
  openGraph: {
    title: "RAI Control Plane — Govern AI Actions at Runtime",
    description:
      "Register agents, authorize tool calls, enforce policies, and produce audit-ready evidence.",
    type: "website",
  },
};

const themeBootstrapScript = `(function(){try{var k="rai_dashboard_theme",t="light",s=localStorage.getItem(k);if(s==="dark"||s==="light")t=s;document.documentElement.dataset.dashboardTheme=t}catch(e){document.documentElement.dataset.dashboardTheme="light"}})();`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrapScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
