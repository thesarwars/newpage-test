import type { Metadata } from "next";
import localFont from "next/font/local";
import { ThemeProvider } from "next-themes";

import "./globals.css";

/**
 * Fonts are vendored, not fetched — see docs/adr/0010.
 *
 * `next/font/google` also self-hosts the result, so the plan's stated reason
 * ("no CDN, see PII") does not distinguish the two. The reason that does is
 * build hermeticity: a production build behind a dead proxy *fails*, and this
 * project runs `pnpm build` in two CI jobs plus `docker compose build`.
 */
const geistSans = localFont({
  src: "./fonts/Geist-Variable.woff2",
  variable: "--font-geist-sans",
  weight: "100 900",
  display: "swap",
});

const geistMono = localFont({
  src: "./fonts/GeistMono-Variable.woff2",
  variable: "--font-geist-mono",
  weight: "100 900",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Career Intelligence Assistant",
  description:
    "Analyse a résumé against job descriptions, with every answer backed by clickable citations into the source document.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      // next-themes writes the resolved theme onto <html> from a synchronous
      // script that runs before React hydrates, so React necessarily sees a
      // mismatch here. This suppresses that one warning and only that one — it
      // applies a single level deep, so it cannot mask a hydration bug in a
      // component.
      suppressHydrationWarning
      // Next 16 stopped overriding scroll-behaviour during SPA navigation; this
      // opts back into snappy route changes while leaving scrollIntoView smooth.
      data-scroll-behavior="smooth"
      className={`${geistSans.variable} ${geistMono.variable} h-full`}
    >
      <body className="min-h-full">
        <ThemeProvider
          // The default, stated explicitly because it decides the CSS selector
          // in globals.css. next-themes writes `data-theme`, NOT `class="dark"`
          // — and Tailwind 4's dark: variant matches neither without the
          // @custom-variant declared there.
          attribute="data-theme"
          defaultTheme="system"
          enableSystem
          // Suppresses transitions while the theme swaps, so switching does not
          // animate every colour on the page through an intermediate state.
          disableTransitionOnChange
        >
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
